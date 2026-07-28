"""ORM models. Importing this package registers all tables on Base.metadata."""

from __future__ import annotations

from app.models.analysis import Candidate, Extraction, Generation, Todo
from app.models.audit import AuditReceipt
from app.models.base import Base
from app.models.channels import ChannelConfig, ChannelThreadState
from app.models.connectors import Connector, ConnectorItem
from app.models.core import (
    Identity,
    Message,
    Part,
    Run,
    Session,
    Tenant,
    User,
)
from app.models.drive import DriveNode, DriveVersion, StorageAccount, StorageBlob
from app.models.effects import ApprovalEnvelope, EffectInvocation
from app.models.events import EventJournal, Outbox
from app.models.files import File
from app.models.github import GithubConnection
from app.models.grants import PermissionGrant
from app.models.knowledge import (
    EmbeddingProfile,
    KnowledgeChunk,
    KnowledgeIngestionJob,
    KnowledgeRetrievalEvidence,
    KnowledgeSource,
    KnowledgeSourceVersion,
)
from app.models.memory import MemoryPassage, UserMemory
from app.models.model_providers import ModelProvider
from app.models.observability import Trace
from app.models.projects import (
    Project,
    ProjectArtifact,
    ProjectChangeSet,
    ProjectChangeSetEntry,
    ProjectImportJob,
    ProjectSandboxRun,
    ProjectSnapshot,
    ProjectSnapshotEntry,
    ProjectSource,
    ProjectWorkingCopy,
    ProjectWorkingCopyEntry,
)
from app.models.schedules import Schedule, ScheduleFiring
from app.models.search import SessionSearchEntry
from app.models.settings import UserSettings

__all__ = [
    "Base",
    "Tenant",
    "User",
    "Identity",
    "Session",
    "Run",
    "Message",
    "Part",
    "EventJournal",
    "Outbox",
    "EffectInvocation",
    "ApprovalEnvelope",
    "Trace",
    "Connector",
    "ConnectorItem",
    "Extraction",
    "Generation",
    "Candidate",
    "Todo",
    "Schedule",
    "ScheduleFiring",
    "UserSettings",
    "AuditReceipt",
    "UserMemory",
    "MemoryPassage",
    "File",
    "ChannelConfig",
    "ChannelThreadState",
    "SessionSearchEntry",
    "StorageAccount",
    "StorageBlob",
    "DriveNode",
    "DriveVersion",
    "PermissionGrant",
    "EmbeddingProfile",
    "KnowledgeSource",
    "KnowledgeSourceVersion",
    "KnowledgeChunk",
    "KnowledgeIngestionJob",
    "KnowledgeRetrievalEvidence",
    "Project",
    "ProjectSnapshot",
    "ProjectSnapshotEntry",
    "ProjectImportJob",
    "ProjectSource",
    "ProjectWorkingCopy",
    "ProjectWorkingCopyEntry",
    "ProjectChangeSet",
    "ProjectChangeSetEntry",
    "ProjectArtifact",
    "ProjectSandboxRun",
    "GithubConnection",
    "ModelProvider",
]
