"""QQ + agentic-email channels: inbound → durable loop → outbound reply (ADR-026/027/028)."""

from __future__ import annotations

from app.channels.email import (
    AgentMailClient,
    EmailChannelClient,
    RecordingEmailClient,
    SentEmailRecord,
    build_email_channel_client,
    verify_svix_signature,
)
from app.channels.inbound import (
    ApprovalCommand,
    Notifier,
    admit_inbound,
    approval_preview_text,
    compose_reply,
    deliver_run_reply,
    ensure_channel_session,
    final_assistant_text,
    find_pending_approval,
    handle_inbound,
    parse_command,
    pending_approval_for_run,
    resolve_over_channel,
)
from app.channels.qq import (
    OutboundMessage,
    QQClient,
    RecordingQQClient,
    build_qq_client,
)

__all__ = [
    "ApprovalCommand",
    "Notifier",
    "admit_inbound",
    "approval_preview_text",
    "compose_reply",
    "deliver_run_reply",
    "ensure_channel_session",
    "final_assistant_text",
    "find_pending_approval",
    "handle_inbound",
    "parse_command",
    "pending_approval_for_run",
    "resolve_over_channel",
    "OutboundMessage",
    "QQClient",
    "RecordingQQClient",
    "build_qq_client",
    "AgentMailClient",
    "EmailChannelClient",
    "RecordingEmailClient",
    "SentEmailRecord",
    "build_email_channel_client",
    "verify_svix_signature",
]
