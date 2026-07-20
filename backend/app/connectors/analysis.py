"""CONNECTOR_ANALYSIS extraction: a Gmail item -> action candidates (ADR-009/018).

A no-tool, structured-output pass over one connector_item. The provider is asked
for a strict JSON list of action candidates (title/priority/confidence/rationale
+ a source excerpt); the result is persisted as the provenance chain
connector_item -> extraction -> generation -> candidate. No tools, no workspace
access; uncertainty is expressed as confidence and an empty list when nothing is
actionable. Deterministic in tests via the mock provider.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import math
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Candidate, ConnectorItem, Extraction, Generation, Trace
from app.providers import Provider, TextDelta

EXTRACTOR_VERSION = "connector_analysis.v1"
OUTPUT_SCHEMA_VERSION = 1
PROMPT_VERSION = "connector_analysis.v1"

SYSTEM_PROMPT = (
    "You analyze a single email and extract concrete, actionable to-do candidates "
    "for the recipient. Respond with ONLY a JSON object of the form "
    '{"candidates": [{"title": string, "description": string|null, '
    '"due_at": string|null (ISO 8601), "priority": "low"|"medium"|"high", '
    '"confidence": number 0..1, "rationale": string, "source_excerpt": string}]}. '
    "Return an empty candidates list if the email needs no action. Do not invent "
    "facts; base everything on the email. Never call tools."
)

_PRIORITIES = {"low", "medium", "high"}


@dataclasses.dataclass(frozen=True)
class ExtractionResult:
    extraction_id: uuid.UUID
    generation_id: uuid.UUID
    status: str
    candidate_count: int


def _estimate_tokens(text: str) -> int:
    return max(0, math.ceil(len(text) / 4))


def _clamp_confidence(value: object) -> Decimal:
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        num = 0.0
    num = min(1.0, max(0.0, num))
    return Decimal(str(round(num, 4)))


def _parse_due_at(value: object) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _bounded(value: object, limit: int) -> str | None:
    if value is None or value == "":
        return None
    return str(value)[:limit]


def parse_candidates(text: str) -> list[dict[str, object]]:
    """Extract the candidates list from a model reply (tolerates ```json fences)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if "```" in cleaned[3:] else cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    payload = json.loads(cleaned[start : end + 1])
    candidates = payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("candidates is not a list")
    return [c for c in candidates if isinstance(c, dict)]


def _email_prompt(item: ConnectorItem) -> str:
    content = item.content_json or {}
    return (
        f"From: {content.get('from', '')}\n"
        f"Date: {content.get('date', '')}\n"
        f"Subject: {content.get('subject', '')}\n\n"
        f"{content.get('snippet', '')}"
    )


async def _next_extraction_version(
    session: AsyncSession, tenant_id: uuid.UUID, connector_item_id: uuid.UUID
) -> int:
    val = await session.scalar(
        select(func.coalesce(func.max(Extraction.extraction_version), 0) + 1).where(
            Extraction.tenant_id == tenant_id,
            Extraction.connector_item_id == connector_item_id,
        )
    )
    return int(val or 1)


async def run_extraction(
    session: AsyncSession,
    *,
    connector_item: ConnectorItem,
    run_id: uuid.UUID,
    provider: Provider,
    provider_name: str,
    model: str,
) -> ExtractionResult:
    """Analyze one connector_item and persist extraction/generation/candidates."""
    tenant_id = connector_item.tenant_id
    started = datetime.datetime.now(datetime.UTC)

    extraction_id = uuid.uuid4()
    session.add(
        Extraction(
            tenant_id=tenant_id,
            id=extraction_id,
            connector_item_id=connector_item.id,
            run_id=run_id,
            extraction_version=await _next_extraction_version(
                session, tenant_id, connector_item.id
            ),
            extractor_version=EXTRACTOR_VERSION,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            status="running",
            started_at=started,
        )
    )
    await session.flush()

    prompt = _email_prompt(connector_item)
    chunks: list[str] = []
    async for event in provider.stream(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        tools=None,
    ):
        if isinstance(event, TextDelta):
            chunks.append(event.text)
    output = "".join(chunks)

    parsed: list[dict[str, object]] = []
    gen_status = "succeeded"
    extraction_status = "succeeded"
    error: str | None = None
    try:
        parsed = parse_candidates(output)
    except (ValueError, json.JSONDecodeError) as exc:
        gen_status = "failed"
        extraction_status = "failed"
        error = f"parse_error: {exc}"

    completed = datetime.datetime.now(datetime.UTC)

    trace_id = uuid.uuid4()
    session.add(
        Trace(
            tenant_id=tenant_id,
            id=trace_id,
            run_id=run_id,
            trace_kind="candidate_extraction",
            status=gen_status,
            tags={"provider": provider_name, "model": model, "candidates": len(parsed)},
            started_at=started,
            ended_at=completed,
        )
    )
    await session.flush()

    generation_id = uuid.uuid4()
    session.add(
        Generation(
            tenant_id=tenant_id,
            id=generation_id,
            trace_id=trace_id,
            run_id=run_id,
            extraction_id=extraction_id,
            purpose="candidate_extraction",
            provider=provider_name,
            model=model,
            prompt_version=PROMPT_VERSION,
            response_schema_version=OUTPUT_SCHEMA_VERSION,
            status=gen_status,
            input_tokens=_estimate_tokens(SYSTEM_PROMPT + prompt),
            output_tokens=_estimate_tokens(output),
            cost_usd=Decimal("0"),
            started_at=started,
            completed_at=completed,
        )
    )
    await session.flush()

    count = 0
    for ordinal, cand in enumerate(parsed):
        title = str(cand.get("title", "")).strip()[:500]
        if not title:
            continue
        dedupe_key = hashlib.sha256(f"{connector_item.id}:{title.lower()}".encode()).hexdigest()
        already = await session.scalar(
            select(Candidate.id).where(
                Candidate.tenant_id == tenant_id, Candidate.dedupe_key == dedupe_key
            )
        )
        if already is not None:
            continue
        priority = str(cand.get("priority", "medium")).lower()
        session.add(
            Candidate(
                tenant_id=tenant_id,
                id=uuid.uuid4(),
                extraction_id=extraction_id,
                generation_id=generation_id,
                ordinal=ordinal,
                dedupe_key=dedupe_key,
                status="pending",
                title=title,
                description=_bounded(cand.get("description"), 65536),
                due_at=_parse_due_at(cand.get("due_at")),
                priority=priority if priority in _PRIORITIES else "medium",
                confidence=_clamp_confidence(cand.get("confidence", 0)),
                rationale_redacted=_bounded(cand.get("rationale"), 16384),
                source_excerpt_redacted=_bounded(cand.get("source_excerpt"), 16384),
            )
        )
        await session.flush()
        count += 1

    extraction = await session.get(Extraction, (tenant_id, extraction_id))
    if extraction is not None:
        extraction.status = extraction_status
        extraction.completed_at = completed
        extraction.error_redacted = error
        await session.flush()

    return ExtractionResult(
        extraction_id=extraction_id,
        generation_id=generation_id,
        status=extraction_status,
        candidate_count=count,
    )
