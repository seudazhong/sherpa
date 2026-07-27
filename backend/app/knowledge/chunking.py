"""Structural chunking (ADR-036, KB2a).

Split a `NormalizedDoc` into bounded, deterministic retrieval chunks that preserve
each source section's heading/page locator. Chunks never span sections (so a chunk
always has one locator). A section under the token target is one chunk; a longer one
is split at sentence boundaries into ~`target_tokens` chunks with a small carried
overlap. Token counts are a language-aware *estimate* (CJK ≈ 1 token/char, else
≈ 1 token / 4 chars) — a target, not a hard constraint (ADR-036 D2: no heavyweight
tokenizer dependency).
"""

from __future__ import annotations

import dataclasses
import hashlib
import re

from app.knowledge.parsers import NormalizedDoc

_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff]")
# Split *after* sentence/clause terminators (CJK + latin) and hard line breaks.
_SENT = re.compile(r"(?<=[。！？!?；;\n])")
_HARD_SPLIT = 1200  # chars: bound a runaway delimiter-free run


@dataclasses.dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    token_estimate: int
    heading_path: str | None
    page: int | None
    char_offset: int
    content_hash: bytes


def estimate_tokens(text: str) -> int:
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _split_units(text: str) -> list[tuple[int, str]]:
    """Sentence-ish units as (offset_in_text, unit); hard-split runaway spans."""
    units: list[tuple[int, str]] = []
    cursor = 0
    for piece in _SENT.split(text):
        if not piece:
            continue
        start = cursor
        run = piece
        while len(run) > _HARD_SPLIT:
            units.append((start, run[:_HARD_SPLIT]))
            run = run[_HARD_SPLIT:]
            start += _HARD_SPLIT
        units.append((start, run))
        cursor += len(piece)
    return units


def chunk_document(
    doc: NormalizedDoc, *, target_tokens: int = 450, overlap_tokens: int = 64
) -> list[Chunk]:
    chunks: list[Chunk] = []
    ordinal = 0
    for section in doc.sections:
        units = _split_units(section.text)
        i = 0
        while i < len(units):
            j = i
            tok = 0
            while j < len(units) and (
                tok == 0 or tok + estimate_tokens(units[j][1]) <= target_tokens
            ):
                tok += estimate_tokens(units[j][1])
                j += 1
            text = "".join(u for _, u in units[i:j]).strip()
            if text:
                chunks.append(
                    Chunk(
                        ordinal=ordinal,
                        text=text,
                        token_estimate=tok,
                        heading_path=section.heading_path,
                        page=section.page,
                        char_offset=units[i][0],
                        content_hash=hashlib.sha256(text.encode("utf-8")).digest(),
                    )
                )
                ordinal += 1
            if j >= len(units):
                break
            # Carry an overlap tail into the next chunk (bounded by overlap_tokens).
            back = 0
            otok = 0
            while (j - 1 - back) > i and otok + estimate_tokens(
                units[j - 1 - back][1]
            ) <= overlap_tokens:
                otok += estimate_tokens(units[j - 1 - back][1])
                back += 1
            i = max(j - back, i + 1)
    return chunks
