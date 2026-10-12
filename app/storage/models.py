"""Firestore document shapes (spec section 12). Decimals are serialised as strings, never floats."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.engine.models import Profile

ProfileStatus = Literal["EMPTY", "EXTRACTED", "CONFIRMED"]
DocumentKind = Literal["payslip", "epf_statement", "loan_statement"]
AuditEventType = Literal[
    "TOOL_CALL", "COMPUTATION", "EXTRACTION_PROPOSED", "EXTRACTION_CONFIRMED", "EXTRACTION_CORRECTED",
    "DOCUMENT_UPLOADED", "DOCUMENT_PURGED", "AUTHZ_DENIED", "VALIDATION_ERROR", "LLM_ERROR",
    "RATE_LIMITED", "UNGROUNDED_FIGURE", "SESSION_PURGED",
]


def utcnow() -> datetime:
    return datetime.now(UTC)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionRecord(_Record):
    session_id: str
    user_id: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    status: Literal["active", "purged"] = "active"
    profile_status: ProfileStatus = "EMPTY"
    locale: str = "en"
    rules_version: str


class FieldProvenance(_Record):
    source: Literal["payslip", "epf_statement", "loan_statement", "user_stated", "user_corrected", "demo"]
    confidence: float = Field(ge=0, le=1)
    corrected_by_user: bool = False


class ProfileRecord(_Record):
    session_id: str
    user_id: str
    status: ProfileStatus
    confirmed_at: datetime | None = None
    profile: Profile
    field_provenance: dict[str, FieldProvenance] = {}


class MessageRecord(_Record):
    message_id: str
    role: Literal["user", "assistant"]
    content: str
    turn_id: str
    created_at: datetime
    request_id: str
    tool_trace: list[dict[str, Any]] = []
    computations: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []


class DocumentRecord(_Record):
    doc_id: str
    session_id: str
    kind: DocumentKind
    content_type: str
    gcs_uri: str
    uploaded_at: datetime | None = None
    expires_at: datetime
    extraction_status: Literal["PENDING_UPLOAD", "UPLOADED", "EXTRACTED", "REJECTED", "PURGED"] = "PENDING_UPLOAD"
    purged_at: datetime | None = None


class PendingAction(_Record):
    action_id: str
    type: Literal["CONFIRM_EXTRACTION"]
    session_id: str
    user_id: str
    proposed_fields: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    status: Literal["PENDING", "APPLIED", "CANCELLED"] = "PENDING"


class AuditEvent(_Record):
    ts: datetime = Field(default_factory=utcnow)
    request_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    event: AuditEventType
    tool: str | None = None
    inputs_echo: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    rules_version: str | None = None
    outcome: str | None = None
    latency_ms: int | None = None


class DemoProfile(_Record):
    demo_id: str
    display_name: str
    story: str
    documents: list[str]
    profile: Profile
    field_provenance: dict[str, FieldProvenance]
