"""Public API request/response models (api.md §3). Closed schemas (extra=forbid)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptRequest(StrictModel):
    client_message_id: uuid.UUID
    text: Annotated[str, Field(min_length=1, max_length=32_000)]


class PromptAdmission(StrictModel):
    session_id: uuid.UUID
    message_id: uuid.UUID
    run_id: uuid.UUID
    admitted_seq: int
    state: Literal["queued"] = "queued"
    event_cursor: str
    events_url: str
