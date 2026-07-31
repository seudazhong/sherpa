"""Chat attachments — Drive references, resolved at admission, replayed on assembly (ADR-043).

An attachment is **never** a second copy of the bytes: the composer uploads (or picks)
the file in Drive first, and a prompt carries only ``{drive_node_id, version}``. This
module owns both ends of that reference:

* :func:`resolve_attachments` — admission-time validation (ownership via the Drive
  service's tenant+user scoping, not trashed, a real file, pinned version, per-file cap,
  count cap) turning refs into the ``parts`` payloads persisted with the user message.
* :func:`render_attachment_content` — assembly-time expansion into provider content
  blocks under a byte budget, degrading **honestly** (an explicit placeholder) when the
  source has no vision, the budget is spent, the file is too large, or the node is gone.

Bytes therefore live only in Drive (quota/`413`/versioning/trash/GC inherited from
ADR-030) and only ever reach the provider — never the journal, an event payload, or a
log line.
"""

from __future__ import annotations

import base64
import dataclasses
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services import CallerContext, ServiceError
from app.services import drive as drive_svc
from app.services.errors import Invalid, NotFound, TooLarge

IMAGE_PART = "image"
FILE_PART = "file_ref"
ATTACHMENT_KINDS = (IMAGE_PART, FILE_PART)

# Image types every supported provider can carry as inline base64.
_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

# Types worth inlining as a bounded text extract; anything else stays a pointer.
_TEXT_TYPES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/yaml",
        "application/javascript",
        "application/x-sh",
        "application/toml",
    }
)


@dataclasses.dataclass(frozen=True)
class AttachmentRef:
    """What the client submits: a Drive node, optionally pinned to a version."""

    drive_node_id: uuid.UUID
    version: int | None = None


@dataclasses.dataclass(frozen=True)
class ResolvedAttachment:
    kind: str  # image | file_ref
    drive_node_id: uuid.UUID
    version: int
    name: str
    content_type: str
    size_bytes: int

    def payload(self) -> dict[str, object]:
        """The `parts.content_redacted` payload (a reference; never bytes)."""
        return {
            "drive_node_id": str(self.drive_node_id),
            "version": self.version,
            "name": self.name,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
        }


def is_image(content_type: str) -> bool:
    return content_type.split(";")[0].strip().lower() in _IMAGE_TYPES


def _is_texty(content_type: str) -> bool:
    base = content_type.split(";")[0].strip().lower()
    return base.startswith("text/") or base in _TEXT_TYPES


def from_payload(kind: str, payload: dict[str, object]) -> ResolvedAttachment:
    """Rebuild an attachment from a persisted part (assembly side)."""
    return ResolvedAttachment(
        kind=kind,
        drive_node_id=uuid.UUID(str(payload.get("drive_node_id"))),
        version=int(str(payload.get("version", 1))),
        name=str(payload.get("name", "attachment")),
        content_type=str(payload.get("content_type", "application/octet-stream")),
        size_bytes=int(str(payload.get("size_bytes", 0))),
    )


async def resolve_attachments(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    refs: list[AttachmentRef],
) -> list[ResolvedAttachment]:
    """Validate + pin the referenced Drive nodes. Raises `ServiceError` (mapped by REST)."""
    if not refs:
        return []
    if len(refs) > settings.chat_max_attachments:
        raise Invalid(f"at most {settings.chat_max_attachments} attachments per message")

    ctx = CallerContext(tenant_id=tenant_id, user_id=user_id, actor="user")
    out: list[ResolvedAttachment] = []
    for ref in refs:
        try:
            node = await drive_svc.get_node(db, ctx, ref.drive_node_id)
        except ServiceError:
            raise NotFound("attachment not found") from None
        if node.node_type != "file" or node.trashed_at is not None:
            raise NotFound("attachment not found")
        version = ref.version if ref.version is not None else node.version
        if version < 1 or version > node.version:
            raise Invalid("attachment version does not exist")
        if node.size_bytes > settings.drive_max_file_bytes:
            raise TooLarge("attachment is too large")
        kind = IMAGE_PART if is_image(node.content_type) else FILE_PART
        if kind == IMAGE_PART and node.size_bytes > settings.chat_attachment_max_image_bytes:
            raise TooLarge("image attachment is too large")
        out.append(
            ResolvedAttachment(
                kind=kind,
                drive_node_id=node.id,
                version=version,
                name=node.name,
                content_type=node.content_type,
                size_bytes=node.size_bytes,
            )
        )
    return out


def _placeholder(att: ResolvedAttachment, why: str) -> dict[str, object]:
    return {
        "type": "text",
        "text": f"[attachment {att.name} ({att.content_type}, {att.size_bytes} bytes): {why}]",
    }


def _text_block(text: str) -> dict[str, object]:
    return {"type": "text", "text": text}


def _image_block(content_type: str, data: bytes) -> dict[str, object]:
    encoded = base64.b64encode(data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{content_type};base64,{encoded}"},
    }


class AssemblyBudget:
    """Bounds the attachment bytes one provider-history assembly may replay."""

    def __init__(self, limit: int | None = None) -> None:
        self.remaining = limit if limit is not None else settings.chat_attachment_assembly_max_bytes

    def take(self, n: int) -> bool:
        if n > self.remaining:
            return False
        self.remaining -= n
        return True


async def render_attachment_content(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    attachment: ResolvedAttachment,
    budget: AssemblyBudget,
    supports_vision: bool,
) -> dict[str, object]:
    """One provider content block for one attachment (never raises: degrade, don't crash)."""
    if attachment.kind == IMAGE_PART and not supports_vision:
        return _placeholder(
            attachment,
            "this model cannot see images — switch the chat's model source or ask me to "
            "read it another way",
        )
    if attachment.kind == IMAGE_PART and (
        attachment.size_bytes > settings.chat_attachment_max_image_bytes
    ):
        return _placeholder(attachment, "image too large to include")
    if not budget.take(attachment.size_bytes):
        return _placeholder(attachment, "omitted — this turn's attachment budget is spent")

    ctx = CallerContext(tenant_id=tenant_id, user_id=user_id, actor="user")
    try:
        content = await drive_svc.read_node_version(
            db, ctx, attachment.drive_node_id, attachment.version
        )
    except ServiceError:
        return _placeholder(attachment, "no longer available in Drive")

    if attachment.kind == IMAGE_PART:
        return _image_block(attachment.content_type, content.data)

    if _is_texty(attachment.content_type):
        limit = settings.chat_attachment_text_extract_bytes
        raw = content.data[:limit]
        text = raw.decode("utf-8", errors="replace")
        truncated = len(content.data) > limit
        header = f"[attachment {attachment.name} ({attachment.content_type})"
        header += f", truncated to the first {limit} bytes]" if truncated else "]"
        return _text_block(f"{header}\n{text}")

    return _placeholder(
        attachment,
        "binary file — read it with drive.read if you need its contents",
    )
