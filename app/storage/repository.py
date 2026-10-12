"""All persistence goes through here. Every read is scoped by user_id; another user's data looks exactly like missing data."""

from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.engine.stock_match import Listing
from app.storage.docdb import DocDB
from app.storage.models import (
    AuditEvent,
    AuditEventType,
    DemoProfile,
    DocumentRecord,
    MessageRecord,
    PendingAction,
    ProfileRecord,
    SessionRecord,
    utcnow,
)

SESSIONS, PROFILES, PENDING, AUDIT = "sessions", "profiles", "pending_actions", "audit_logs"
PROPERTY_STOCK, DEMO_PROFILES = "property_stock", "demo_profiles"

# Never persisted anywhere (spec section 12). Matched case-insensitively at any depth.
FORBIDDEN_KEYS = frozenset({
    "raw_text", "ocr_text", "document_text", "document_bytes", "image", "image_bytes",
    "ic", "ic_number", "nric", "national_id", "bank_account", "account_number",
    "full_name", "employer_address", "address",
})
_LISTING_NUMERIC = ("price", "maintenance_monthly", "distance_to_kl_city_km")


class NotFoundError(Exception):
    def __init__(self, what: str) -> None:
        super().__init__(f"{what} not found")
        self.what = what


class AuthzDeniedError(NotFoundError):
    """Belongs to another user. Callers must respond exactly as for NotFoundError."""


class ActionExpiredError(Exception):
    pass


class ActionNotPendingError(Exception):
    pass


def messages_path(session_id: str) -> str:
    return f"{SESSIONS}/{session_id}/messages"


def documents_path(session_id: str) -> str:
    return f"{SESSIONS}/{session_id}/documents"


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(12)}"


def to_doc(value: Any) -> Any:
    """Firestore-safe: Decimal -> str (exact), pydantic -> dict; datetimes stay native for TTL policies."""
    if isinstance(value, BaseModel):
        return to_doc(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_doc(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_doc(v) for v in value]
    return value


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if str(k).casefold() not in FORBIDDEN_KEYS}
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    return value


class Repository:
    def __init__(self, db: DocDB) -> None:
        self.db = db

    # --- sessions ------------------------------------------------------------------------

    async def create_session(self, user_id: str, rules_version: str, ttl: timedelta, locale: str = "en") -> SessionRecord:
        now = utcnow()
        rec = SessionRecord(
            session_id=new_id("ses"), user_id=user_id, created_at=now, updated_at=now,
            expires_at=now + ttl, locale=locale, rules_version=rules_version,
        )
        await self.db.set(SESSIONS, rec.session_id, to_doc(rec))
        return rec

    async def get_session(self, session_id: str, user_id: str) -> SessionRecord:
        raw = await self.db.get(SESSIONS, session_id)
        if raw is None:
            raise NotFoundError("session")
        rec = SessionRecord.model_validate(raw)
        if rec.user_id != user_id:
            await self.write_audit(AuditEvent(event="AUTHZ_DENIED", session_id=session_id, user_id=user_id,
                                              outcome="session"))
            raise AuthzDeniedError("session")
        if rec.status != "active" or rec.expires_at <= utcnow():
            raise NotFoundError("session")
        return rec

    async def update_session(self, rec: SessionRecord, **changes: Any) -> SessionRecord:
        updated = rec.model_copy(update={**changes, "updated_at": utcnow()})
        await self.db.set(SESSIONS, rec.session_id, to_doc(updated))
        return updated

    # --- profiles ------------------------------------------------------------------------

    async def save_profile(self, rec: ProfileRecord) -> None:
        await self.db.set(PROFILES, rec.session_id, scrub(to_doc(rec)))

    async def get_profile(self, session_id: str, user_id: str) -> ProfileRecord | None:
        raw = await self.db.get(PROFILES, session_id)
        if raw is None:
            return None
        rec = ProfileRecord.model_validate(raw)
        if rec.user_id != user_id:
            raise AuthzDeniedError("profile")
        return rec

    # --- messages ------------------------------------------------------------------------

    async def add_message(self, session_id: str, msg: MessageRecord) -> None:
        await self.db.set(messages_path(session_id), msg.message_id, scrub(to_doc(msg)))

    async def list_messages(self, session_id: str, limit: int = 50) -> list[MessageRecord]:
        rows = await self.db.query(messages_path(session_id), order_by="created_at", descending=True, limit=limit)
        return [MessageRecord.model_validate(doc) for _, doc in reversed(rows)]

    # --- documents -----------------------------------------------------------------------

    async def save_document(self, rec: DocumentRecord) -> None:
        await self.db.set(documents_path(rec.session_id), rec.doc_id, to_doc(rec))

    async def get_document(self, session_id: str, doc_id: str) -> DocumentRecord:
        raw = await self.db.get(documents_path(session_id), doc_id)
        if raw is None:
            raise NotFoundError("document")
        return DocumentRecord.model_validate(raw)

    async def list_documents(self, session_id: str) -> list[DocumentRecord]:
        return [DocumentRecord.model_validate(d) for _, d in await self.db.query(documents_path(session_id))]

    # --- pending actions -----------------------------------------------------------------

    async def create_pending_action(
        self, session: SessionRecord, proposed_fields: dict[str, Any], ttl: timedelta,
    ) -> PendingAction:
        now = utcnow()
        action = PendingAction(
            action_id=new_id("act"), type="CONFIRM_EXTRACTION", session_id=session.session_id,
            user_id=session.user_id, proposed_fields=proposed_fields, created_at=now, expires_at=now + ttl,
        )
        await self.db.set(PENDING, action.action_id, scrub(to_doc(action)))
        return action

    async def get_pending_action(self, action_id: str, user_id: str) -> PendingAction:
        raw = await self.db.get(PENDING, action_id)
        if raw is None:
            raise NotFoundError("action")
        action = PendingAction.model_validate(raw)
        if action.user_id != user_id:
            await self.write_audit(AuditEvent(event="AUTHZ_DENIED", session_id=action.session_id, user_id=user_id,
                                              outcome="action"))
            raise AuthzDeniedError("action")
        if action.status != "PENDING":
            raise ActionNotPendingError(action.status)
        if action.expires_at <= utcnow():
            raise ActionExpiredError(action_id)
        return action

    async def set_action_status(self, action: PendingAction, status: str) -> None:
        await self.db.update(PENDING, action.action_id, {"status": status})

    # --- audit ---------------------------------------------------------------------------

    async def write_audit(self, event: AuditEvent) -> None:
        await self.db.add(AUDIT, scrub(to_doc(event)))

    async def list_audit(
        self, session_id: str | None = None, event: AuditEventType | None = None,
    ) -> list[AuditEvent]:
        where: list[Any] = []
        if session_id is not None:
            where.append(("session_id", "==", session_id))
        if event is not None:
            where.append(("event", "==", event))
        rows = await self.db.query(AUDIT, where=where, order_by="ts")
        return [AuditEvent.model_validate(d) for _, d in rows]

    # --- purge ---------------------------------------------------------------------------

    async def purge_session(self, session: SessionRecord, request_id: str | None = None) -> dict[str, int]:
        """Delete profile, messages, document records and pending actions; mark the session purged."""
        sid, now = session.session_id, utcnow()
        docs = await self.list_documents(sid)
        for doc in docs:
            await self.write_audit(AuditEvent(event="DOCUMENT_PURGED", request_id=request_id, session_id=sid,
                                              user_id=session.user_id, outputs={"doc_id": doc.doc_id, "kind": doc.kind}))
        counts = {
            "documents": await self.db.delete_all(documents_path(sid)),
            "messages": await self.db.delete_all(messages_path(sid)),
            "profile": int(await self.db.get(PROFILES, sid) is not None),
            "pending_actions": 0,
        }
        await self.db.delete(PROFILES, sid)
        for action_id, _ in await self.db.query(PENDING, where=[("session_id", "==", sid)]):
            await self.db.delete(PENDING, action_id)
            counts["pending_actions"] += 1
        await self.db.set(SESSIONS, sid, to_doc(session.model_copy(update={
            "status": "purged", "profile_status": "EMPTY", "updated_at": now, "expires_at": now,
        })))
        await self.write_audit(AuditEvent(event="SESSION_PURGED", request_id=request_id, session_id=sid,
                                          user_id=session.user_id, outputs=counts))
        return counts

    # --- reference data ------------------------------------------------------------------

    async def replace_property_stock(self, listings: list[Listing]) -> int:
        await self.db.delete_all(PROPERTY_STOCK)
        for listing in listings:
            doc = to_doc(listing)
            # Stored as numbers so Firestore can index and range-query them.
            for key in _LISTING_NUMERIC:
                doc[key] = float(doc[key])
            await self.db.set(PROPERTY_STOCK, listing.listing_id, doc)
        return len(listings)

    async def list_property_stock(self, area: str | None = None, max_price: Decimal | None = None) -> list[Listing]:
        where: list[Any] = []
        if area is not None:
            where.append(("area", "==", area))
        if max_price is not None:
            where.append(("price", "<=", float(max_price)))
        rows = await self.db.query(PROPERTY_STOCK, where=where)
        return [Listing.model_validate({**d, **{k: Decimal(str(d[k])) for k in _LISTING_NUMERIC}}) for _, d in rows]

    async def replace_demo_profiles(self, profiles: list[DemoProfile]) -> int:
        await self.db.delete_all(DEMO_PROFILES)
        for p in profiles:
            await self.db.set(DEMO_PROFILES, p.demo_id, to_doc(p))
        return len(profiles)

    async def list_demo_profiles(self) -> list[DemoProfile]:
        return [DemoProfile.model_validate(d) for _, d in await self.db.query(DEMO_PROFILES, order_by="demo_id")]

    async def get_demo_profile(self, demo_id: str) -> DemoProfile:
        raw = await self.db.get(DEMO_PROFILES, demo_id)
        if raw is None:
            raise NotFoundError("demo_profile")
        return DemoProfile.model_validate(raw)
