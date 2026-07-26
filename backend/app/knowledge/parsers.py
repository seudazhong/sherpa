"""Bounded, no-tool document parsers (ADR-036, KB2a).

Turn an uploaded file's bytes into a `NormalizedDoc`: a list of `DocSection`s with
page/heading locators, plus a coarse detected language. Parsing is a pure,
deterministic, CPU-only operation with no tools, no remote fetch, and no active
document behaviour (ADR-009/019). Unsupported or empty inputs raise a `ParseError`
with a bounded, named `code` that becomes the version's `failure_code`.

Supported (v1): PDF (`pypdf`), DOCX (`python-docx`), Markdown, plain text. Scanned
(image-only) PDFs, archives, OCR, and other formats are deferred.
"""

from __future__ import annotations

import dataclasses
import io
import re

from docx import Document
from pypdf import PdfReader

_PDF_TYPES = {"application/pdf"}
_DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_MD_TYPES = {"text/markdown", "text/x-markdown"}
_TXT_TYPES = {"text/plain"}

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff]")


class ParseError(Exception):
    """A bounded, named parse failure (becomes `knowledge_source_versions.failure_code`)."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code


@dataclasses.dataclass(frozen=True)
class DocSection:
    heading_path: str | None
    page: int | None
    text: str


@dataclasses.dataclass(frozen=True)
class NormalizedDoc:
    sections: list[DocSection]
    language: str | None


def detect_language(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    cjk = len(_CJK.findall(stripped))
    return "zh" if cjk / max(len(stripped), 1) > 0.15 else "en"


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def parse_txt(data: bytes) -> NormalizedDoc:
    text = _decode(data).replace("\r\n", "\n").strip()
    if not text:
        raise ParseError("empty_document")
    return NormalizedDoc([DocSection(None, None, text)], detect_language(text))


def parse_markdown(data: bytes) -> NormalizedDoc:
    lines = _decode(data).replace("\r\n", "\n").split("\n")
    sections: list[DocSection] = []
    trail: list[str] = []  # heading trail by level
    heading_path: str | None = None
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append(DocSection(heading_path, None, body))
        buf.clear()

    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            trail[:] = trail[: level - 1]
            while len(trail) < level - 1:
                trail.append("")
            trail.append(title)
            heading_path = " / ".join(t for t in trail if t)
        else:
            buf.append(line)
    flush()
    if not sections:
        raise ParseError("empty_document")
    full = "\n".join(s.text for s in sections)
    return NormalizedDoc(sections, detect_language(full))


def parse_docx(data: bytes) -> NormalizedDoc:
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - bounded to a named failure
        raise ParseError("corrupt_docx", str(exc)) from None
    sections: list[DocSection] = []
    trail: list[str] = []
    heading_path: str | None = None
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body:
            sections.append(DocSection(heading_path, None, body))
        buf.clear()

    for para in doc.paragraphs:
        text = para.text.strip()
        style = (para.style.name or "") if para.style else ""
        if text and style.startswith("Heading"):
            flush()
            m = re.search(r"(\d+)", style)
            level = int(m.group(1)) if m else 1
            trail[:] = trail[: level - 1]
            while len(trail) < level - 1:
                trail.append("")
            trail.append(text)
            heading_path = " / ".join(t for t in trail if t)
        elif text:
            buf.append(text)
    flush()
    if not sections:
        raise ParseError("empty_document")
    full = "\n".join(s.text for s in sections)
    return NormalizedDoc(sections, detect_language(full))


def parse_pdf(data: bytes) -> NormalizedDoc:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - bounded to a named failure
        raise ParseError("corrupt_pdf", str(exc)) from None
    sections: list[DocSection] = []
    for i, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 - a bad page must not crash ingestion
            text = ""
        if text:
            sections.append(DocSection(None, i + 1, text))
    if not sections:
        # No extractable text layer — almost always a scanned/image-only PDF.
        raise ParseError("unsupported_scanned_pdf")
    full = "\n".join(s.text for s in sections)
    return NormalizedDoc(sections, detect_language(full))


def parse_document(
    data: bytes, *, content_type: str | None = None, filename: str | None = None
) -> NormalizedDoc:
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = ""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[1].lower()
    if ct in _PDF_TYPES or ext == "pdf":
        return parse_pdf(data)
    if ct in _DOCX_TYPES or ext == "docx":
        return parse_docx(data)
    if ct in _MD_TYPES or ext in ("md", "markdown"):
        return parse_markdown(data)
    if ct in _TXT_TYPES or ext in ("txt", "text", "log", ""):
        return parse_txt(data)
    raise ParseError("unsupported_type", f"content_type={ct or '?'} ext={ext or '?'}")
