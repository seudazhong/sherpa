// Bounded client-side folder/multi-file upload for Drive (ADR-042).
//
// There is no batch or archive endpoint: we walk the picked directory (or the
// dropped entry tree), recreate it level by level with POST /drive/folders
// (409 => "already exists, reuse"), then upload each file with POST /drive/files.
// The batch is bounded before anything is sent, uploads run with small
// concurrency, and per-file outcomes are reported individually — a batch is not
// a transaction, so we never pretend it rolled back.

import { ApiError, api, type DriveNode } from "../api";

export const UPLOAD_MAX_FILES = 200;
export const UPLOAD_MAX_TOTAL_BYTES = 200 * 1024 * 1024;
export const UPLOAD_CONCURRENCY = 3;

export interface PickedFile {
  file: File;
  /** Folder segments relative to the upload target (empty = target folder). */
  dirs: string[];
  /**
   * Base name to store under. A directory-picked file carries its **relative path**
   * as the multipart filename, which the server rejects (names may not contain "/"),
   * so we always send the base name explicitly.
   */
  name: string;
}

export type UploadStatus =
  | "queued"
  | "uploading"
  | "done"
  | "failed"
  | "skipped";

export interface UploadItem {
  id: string;
  name: string;
  path: string;
  size: number;
  status: UploadStatus;
  error?: string;
}

export interface BatchSummary {
  done: number;
  failed: number;
  skipped: number;
  /** Set when the quota ran out — the remaining queue was abandoned on purpose. */
  outOfSpace: boolean;
}

export class TooManyFilesError extends Error {
  constructor(public count: number) {
    super(`too many files: ${count}`);
  }
}

export class BatchTooLargeError extends Error {
  constructor(public bytes: number) {
    super(`batch too large: ${bytes}`);
  }
}

/** The base name of a file whose `name` may be a relative path. */
export function baseName(name: string): string {
  const parts = name.split(/[\\/]/);
  return parts[parts.length - 1] || name;
}

/** Files chosen through <input multiple> / <input webkitdirectory>. */
export function pickedFromInput(files: FileList | null): PickedFile[] {
  const out: PickedFile[] = [];
  for (const file of Array.from(files ?? [])) {
    const rel = (file as File & { webkitRelativePath?: string })
      .webkitRelativePath;
    const dirs = rel ? rel.split("/").slice(0, -1) : [];
    out.push({ file, dirs, name: baseName(rel || file.name) });
  }
  return out;
}

function readEntries(
  reader: FileSystemDirectoryReader,
): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) =>
    reader.readEntries((entries) => resolve(entries), reject),
  );
}

function entryFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function walkEntry(
  entry: FileSystemEntry,
  dirs: string[],
  out: PickedFile[],
): Promise<void> {
  if (out.length > UPLOAD_MAX_FILES) return; // bounded: stop early, caller rejects
  if (entry.isFile) {
    const file = await entryFile(entry as FileSystemFileEntry);
    out.push({ file, dirs, name: baseName(file.name) });
    return;
  }
  const reader = (entry as FileSystemDirectoryEntry).createReader();
  // readEntries returns at most ~100 entries per call; drain it.
  for (;;) {
    const batch = await readEntries(reader);
    if (batch.length === 0) break;
    for (const child of batch) {
      await walkEntry(child, [...dirs, entry.name], out);
      if (out.length > UPLOAD_MAX_FILES) return;
    }
  }
}

/** Files (and whole folders) dropped onto a drop target. */
export async function pickedFromDataTransfer(
  dt: DataTransfer,
): Promise<PickedFile[]> {
  const entries: FileSystemEntry[] = [];
  for (const item of Array.from(dt.items)) {
    if (item.kind !== "file") continue;
    const entry = item.webkitGetAsEntry?.();
    if (entry) entries.push(entry);
  }
  if (entries.length === 0) return pickedFromInput(dt.files);

  const out: PickedFile[] = [];
  for (const entry of entries) {
    if (entry.isFile) {
      const file = await entryFile(entry as FileSystemFileEntry);
      out.push({ file, dirs: [], name: baseName(file.name) });
    } else {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      for (;;) {
        const batch = await readEntries(reader);
        if (batch.length === 0) break;
        for (const child of batch) await walkEntry(child, [entry.name], out);
      }
    }
    if (out.length > UPLOAD_MAX_FILES) break;
  }
  return out;
}

/** Reject an oversized batch before a single byte is sent. */
export function assertWithinBounds(picked: PickedFile[]): void {
  if (picked.length > UPLOAD_MAX_FILES)
    throw new TooManyFilesError(picked.length);
  const total = picked.reduce((n, p) => n + p.file.size, 0);
  if (total > UPLOAD_MAX_TOTAL_BYTES) throw new BatchTooLargeError(total);
}

export function toItems(picked: PickedFile[]): UploadItem[] {
  return picked.map((p, i) => ({
    id: `${i}-${p.dirs.join("/")}/${p.name}`,
    name: p.name,
    path: [...p.dirs, p.name].join("/"),
    size: p.file.size,
    status: "queued" as const,
  }));
}

async function findChildFolder(
  parentId: string | null,
  name: string,
): Promise<DriveNode | null> {
  let cursor: string | null | undefined;
  do {
    const page = await api.driveList({
      parent: parentId,
      limit: 200,
      cursor: cursor ?? undefined,
    });
    const hit = page.items.find(
      (n) => n.node_type === "folder" && n.name === name,
    );
    if (hit) return hit;
    cursor = page.next_cursor;
  } while (cursor);
  return null;
}

/** Create the folder, or reuse the existing one when the name is taken (409). */
export async function ensureFolder(
  csrf: string,
  parentId: string | null,
  name: string,
): Promise<string> {
  try {
    const node = await api.driveCreateFolder(csrf, parentId, name);
    return node.id;
  } catch (e) {
    if ((e as ApiError).status !== 409) throw e;
    const existing = await findChildFolder(parentId, name);
    if (!existing) throw e;
    return existing.id;
  }
}

export function uploadErrorText(e: unknown): string {
  const status = (e as ApiError).status;
  if (status === 507) return "Not enough storage space";
  if (status === 413) return "File is too large";
  if (status === 409) return "A file with that name already exists";
  return "Upload failed";
}

export interface BatchOptions {
  csrf: string;
  parentId: string | null;
  picked: PickedFile[];
  onUpdate: (items: UploadItem[]) => void;
}

/**
 * Recreate the folder tree, then upload every file with bounded concurrency.
 * Returns per-file outcomes; a `507` stops the remaining queue (skipped), since
 * every further upload would fail the same way.
 */
export async function uploadBatch({
  csrf,
  parentId,
  picked,
  onUpdate,
}: BatchOptions): Promise<BatchSummary> {
  const items = toItems(picked);
  const emit = () => onUpdate([...items]);
  emit();

  // Mirror the folder tree lazily: parents before children, each path once.
  const folderIds = new Map<string, string | null>([["", parentId]]);
  const pending = new Map<string, Promise<string | null>>();

  const ensureDirs = async (dirs: string[]): Promise<string | null> => {
    let key = "";
    let current = parentId;
    for (const segment of dirs) {
      const next = key ? `${key}/${segment}` : segment;
      if (folderIds.has(next)) {
        current = folderIds.get(next) ?? null;
      } else {
        const parent = current;
        let inflight = pending.get(next);
        if (!inflight) {
          inflight = ensureFolder(csrf, parent, segment);
          pending.set(next, inflight);
        }
        current = await inflight;
        folderIds.set(next, current);
      }
      key = next;
    }
    return current;
  };

  let outOfSpace = false;
  let cursor = 0;

  const worker = async (): Promise<void> => {
    for (;;) {
      const index = cursor++;
      if (index >= picked.length) return;
      const item = items[index];
      if (outOfSpace) {
        item.status = "skipped";
        item.error = "Skipped — out of storage space";
        emit();
        continue;
      }
      item.status = "uploading";
      emit();
      try {
        const folderId = await ensureDirs(picked[index].dirs);
        await api.driveUpload(
          csrf,
          folderId,
          picked[index].file,
          picked[index].name,
        );
        item.status = "done";
      } catch (e) {
        item.status = "failed";
        item.error = uploadErrorText(e);
        if ((e as ApiError).status === 507) outOfSpace = true;
      }
      emit();
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(UPLOAD_CONCURRENCY, picked.length) }, worker),
  );

  return {
    done: items.filter((i) => i.status === "done").length,
    failed: items.filter((i) => i.status === "failed").length,
    skipped: items.filter((i) => i.status === "skipped").length,
    outOfSpace,
  };
}
