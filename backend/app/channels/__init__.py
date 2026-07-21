"""IM / QQ channel: inbound webhook → durable loop → outbound reply (milestone 4)."""

from __future__ import annotations

from app.channels.inbound import (
    ApprovalCommand,
    admit_inbound,
    approval_preview_text,
    compose_reply,
    deliver_run_reply,
    ensure_channel_session,
    final_assistant_text,
    find_pending_approval,
    parse_command,
    pending_approval_for_run,
)
from app.channels.qq import (
    OneBotQQClient,
    OutboundMessage,
    QQClient,
    RecordingQQClient,
    build_qq_client,
    sign_body,
    verify_signature,
)

__all__ = [
    "ApprovalCommand",
    "admit_inbound",
    "approval_preview_text",
    "compose_reply",
    "deliver_run_reply",
    "ensure_channel_session",
    "final_assistant_text",
    "find_pending_approval",
    "parse_command",
    "pending_approval_for_run",
    "OneBotQQClient",
    "OutboundMessage",
    "QQClient",
    "RecordingQQClient",
    "build_qq_client",
    "sign_body",
    "verify_signature",
]
