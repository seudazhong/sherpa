"""Multimodal user content, normalized once for every wire format (ADR-043).

The loop assembles history in the **OpenAI shape**: a user message's `content` is
either a plain string (text-only turn — unchanged, so cached prefixes stay stable) or
a list of `{"type": "text"}` / `{"type": "image_url"}` blocks whose image URL is a
`data:` URL. Each provider adapter translates that shape into its own; this module
parses it once so the three adapters do not each re-implement data-URL handling.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

_DATA_URL = re.compile(r"^data:([^;,]+);base64,(.*)$", re.DOTALL)


@dataclasses.dataclass(frozen=True)
class TextBlock:
    text: str


@dataclasses.dataclass(frozen=True)
class ImageBlock:
    media_type: str
    data_b64: str


ContentBlock = TextBlock | ImageBlock


def parse_data_url(url: str) -> tuple[str, str] | None:
    """`data:image/png;base64,AAAA` → `("image/png", "AAAA")`; None when not a data URL."""
    m = _DATA_URL.match(url or "")
    if not m:
        return None
    return m.group(1), m.group(2)


def normalize_content(content: object) -> list[ContentBlock]:
    """OpenAI-shape message content → canonical blocks (a string becomes one TextBlock).

    Unknown block types degrade to text so a provider never receives something it cannot
    render; a remote (non-`data:`) image URL is not fetched — it is described instead,
    since the assembler only ever produces data URLs (ADR-043).
    """
    if content is None:
        return []
    if isinstance(content, str):
        return [TextBlock(content)] if content else []
    if not isinstance(content, list):
        return [TextBlock(str(content))]

    out: list[ContentBlock] = []
    for raw in content:
        if not isinstance(raw, dict):
            out.append(TextBlock(str(raw)))
            continue
        block: dict[str, Any] = raw
        btype = block.get("type")
        if btype == "text":
            out.append(TextBlock(str(block.get("text", ""))))
        elif btype == "image_url":
            url = block.get("image_url")
            url_str = str(url.get("url", "")) if isinstance(url, dict) else str(url or "")
            parsed = parse_data_url(url_str)
            if parsed is None:
                out.append(TextBlock("[image omitted: unsupported reference]"))
            else:
                out.append(ImageBlock(media_type=parsed[0], data_b64=parsed[1]))
        else:
            out.append(TextBlock(str(block.get("text", "")) or f"[{btype} block]"))
    return out


def flatten_text(content: object) -> str:
    """Text-only rendering of a message content (providers without image support)."""
    parts: list[str] = []
    for block in normalize_content(content):
        if isinstance(block, TextBlock):
            parts.append(block.text)
        else:
            parts.append(f"[image: {block.media_type}]")
    return "\n".join(p for p in parts if p)
