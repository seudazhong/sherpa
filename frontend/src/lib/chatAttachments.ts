// Chat composer attachments (ADR-043).
//
// Drive is the only byte store: a pasted/uploaded image is written to Drive under a
// "Chat uploads" folder before the prompt is admitted, so it inherits quota, per-file
// caps, versioning, trash, and GC. The prompt then carries only {drive_node_id,
// version}. Picking an existing Drive file skips the upload entirely.

import { ApiError, api, type DriveNode } from "../api";
import { baseName, ensureFolder, uploadErrorText } from "./driveUpload";

export const CHAT_UPLOADS_FOLDER = "Chat uploads";
export const MAX_ATTACHMENTS = 8;

export interface Attachment {
  drive_node_id: string;
  version: number;
  name: string;
  content_type: string;
  size_bytes: number;
}

export function isImage(contentType: string): boolean {
  return ["image/png", "image/jpeg", "image/gif", "image/webp"].includes(
    contentType.split(";")[0].trim().toLowerCase(),
  );
}

export function toAttachment(node: DriveNode): Attachment {
  return {
    drive_node_id: node.id,
    version: node.version,
    name: node.name,
    content_type: node.content_type,
    size_bytes: node.size_bytes,
  };
}

/** Upload a composer file into the "Chat uploads" Drive folder. */
export async function uploadToChatFolder(
  csrf: string,
  file: File,
): Promise<Attachment> {
  const folderId = await ensureFolder(csrf, null, CHAT_UPLOADS_FOLDER);
  // A pasted image often has no name; keep it unique so we never hit a 409.
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const name = file.name?.trim()
    ? `${stamp}-${baseName(file.name.trim())}`
    : `${stamp}-pasted.png`;
  const node = await api.driveUpload(csrf, folderId, file, name);
  return toAttachment(node);
}

export function attachmentErrorText(e: unknown): string {
  const status = (e as ApiError).status;
  if (status === 404) return "That file is no longer in your Drive.";
  return uploadErrorText(e) + ".";
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
