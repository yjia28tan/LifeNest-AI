"""The extraction review gate (spec principle 3): Gemini proposes, the user confirms or corrects, only then compute."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from app.agent.state import (
    apply_confirmation,
    build_profile,
    derive_status,
    flatten_profile,
    validate_proposed,
)
from app.config import Settings
from app.errors import ApiError
from app.llm.extractor import DocumentExtractor, ExtractorError
from app.storage.documents import (
    DocumentNotFoundError,
    DocumentStore,
    InvalidDocumentError,
    validate_document,
)
from app.storage.models import (
    AuditEvent,
    DocumentRecord,
    ProfileRecord,
    ProfileStatus,
    SessionRecord,
    utcnow,
)
from app.storage.repository import Repository

NOT_SUPPORTED_MESSAGE = (
    "This doesn't look like a payslip, EPF statement or loan statement. "
    "Please upload your latest payslip and your EPF statement."
)
UNREADABLE_MESSAGE = (
    "We couldn't read this document clearly. Please retake the photo flat, in good light, with the whole page visible."
)


async def refresh_status(repo: Repository, session: SessionRecord) -> tuple[SessionRecord, ProfileRecord | None]:
    record = await repo.get_profile(session.session_id, session.user_id)
    pending = len(await repo.list_pending_actions(session.session_id))
    status: ProfileStatus = derive_status(record.profile if record else None,
                                          record.confirmed_fields if record else {}, pending)
    if record is not None and record.status != status:
        record = record.model_copy(update={"status": status})
        await repo.save_profile(record)
    if session.profile_status != status:
        session = await repo.update_session(session, profile_status=status)
    return session, record


def profile_payload(session: SessionRecord, record: ProfileRecord | None) -> dict[str, Any]:
    _, missing = build_profile(record.confirmed_fields if record else {})
    return {
        "profile_status": session.profile_status,
        "profile": record.profile.model_dump(mode="json") if record and record.profile else None,
        "missing_fields": missing,
        "field_provenance": {k: v.model_dump() for k, v in (record.field_provenance if record else {}).items()},
    }


async def propose_extraction(
    *,
    repo: Repository,
    documents: DocumentStore,
    extractor: DocumentExtractor,
    settings: Settings,
    session: SessionRecord,
    doc: DocumentRecord,
    request_id: str,
) -> dict[str, Any]:
    def audit(event: str, **kw: Any) -> AuditEvent:
        return AuditEvent(event=event, request_id=request_id, session_id=session.session_id,  # type: ignore[arg-type]
                          user_id=session.user_id, **kw)

    try:
        data = await documents.read(session.session_id, doc.doc_id)
        validate_document(data, doc.content_type, settings.max_document_bytes)
    except DocumentNotFoundError:
        raise ApiError(422, "DOCUMENT_UNREADABLE", "No file has been uploaded for this document yet.") from None
    except InvalidDocumentError:
        raise ApiError(422, "DOCUMENT_UNREADABLE", UNREADABLE_MESSAGE) from None

    if doc.extraction_status == "PENDING_UPLOAD":
        doc = doc.model_copy(update={"extraction_status": "UPLOADED", "uploaded_at": utcnow()})
        await repo.save_document(doc)
        await repo.write_audit(audit("DOCUMENT_UPLOADED", outputs={"doc_id": doc.doc_id, "kind": doc.kind}))

    try:
        result = await asyncio.wait_for(extractor.extract(data, doc.content_type, doc.kind),
                                        timeout=settings.llm_timeout_seconds)
    except TimeoutError:
        await repo.write_audit(audit("LLM_ERROR", tool="extract_document", outcome="TIMEOUT"))
        raise ApiError(504, "UPSTREAM_TIMEOUT", "Reading the document took too long. Please try again.") from None
    except ExtractorError as exc:
        await repo.write_audit(audit("LLM_ERROR", tool="extract_document", outcome=str(exc)[:80]))
        raise ApiError(422, "DOCUMENT_UNREADABLE", UNREADABLE_MESSAGE) from None

    if result.kind_detected == "unsupported":
        await repo.save_document(doc.model_copy(update={"extraction_status": "REJECTED"}))
        await repo.write_audit(audit("EXTRACTION_PROPOSED", outcome="NOT_A_SUPPORTED_DOCUMENT",
                                     outputs={"doc_id": doc.doc_id}))
        raise ApiError(422, "NOT_A_SUPPORTED_DOCUMENT", NOT_SUPPORTED_MESSAGE)

    accepted, warnings = validate_proposed(result.fields, settings.extraction_min_confidence)
    if not result.readable or not accepted:
        await repo.save_document(doc.model_copy(update={"extraction_status": "REJECTED"}))
        await repo.write_audit(audit("EXTRACTION_PROPOSED", outcome="DOCUMENT_UNREADABLE",
                                     outputs={"doc_id": doc.doc_id}))
        raise ApiError(422, "DOCUMENT_UNREADABLE", UNREADABLE_MESSAGE)
    if result.kind_detected != doc.kind:
        warnings.append(f"kind_mismatch:{result.kind_detected}")

    fields = {p: {"value": f.value, "confidence": f.confidence, "source_hint": f.source_hint}
              for p, f in accepted.items()}
    action = await repo.create_pending_action(
        session,
        {"doc_id": doc.doc_id, "kind": result.kind_detected, "fields": fields, "warnings": warnings},
        timedelta(seconds=settings.pending_action_ttl_seconds),
    )
    await repo.save_document(doc.model_copy(update={"extraction_status": "EXTRACTED"}))
    session, _ = await refresh_status(repo, session)
    await repo.write_audit(audit("EXTRACTION_PROPOSED", outcome="PROPOSED", outputs={
        "doc_id": doc.doc_id, "kind": result.kind_detected, "action_id": action.action_id,
        "paths": sorted(fields), "warnings": warnings,
    }))
    return {
        "doc_id": doc.doc_id,
        "kind_detected": result.kind_detected,
        "fields": [{"path": p, **f} for p, f in fields.items()],
        "action_id": action.action_id,
        "expires_at": action.expires_at,
        "warnings": warnings,
        "profile_status": session.profile_status,
    }


async def confirm_extraction(
    *, repo: Repository, action_id: str, user_id: str, corrections: dict[str, Any], request_id: str,
) -> dict[str, Any]:
    action = await repo.get_pending_action(action_id, user_id)
    session = await repo.get_session(action.session_id, user_id)
    record = await repo.get_profile(session.session_id, user_id)

    # Raises FieldValueError (422) before anything is written, so a bad correction leaves the action pending.
    fields, provenance, corrected = apply_confirmation(
        record.confirmed_fields if record else {},
        record.field_provenance if record else {},
        action.proposed_fields["fields"],
        action.proposed_fields["kind"],
        corrections,
    )
    profile, _ = build_profile(fields)

    await repo.set_action_status(action, "APPLIED")
    await repo.save_profile(ProfileRecord(
        session_id=session.session_id, user_id=user_id, status=record.status if record else "EXTRACTED",
        confirmed_at=utcnow(), confirmed_fields=fields, profile=profile, field_provenance=provenance,
    ))
    session, record = await refresh_status(repo, session)

    base = {"request_id": request_id, "session_id": session.session_id, "user_id": user_id}
    await repo.write_audit(AuditEvent(event="EXTRACTION_CONFIRMED", **base, outcome=session.profile_status,
                                      outputs={"action_id": action_id, "paths": sorted(action.proposed_fields["fields"])}))
    for path in corrected:
        await repo.write_audit(AuditEvent(event="EXTRACTION_CORRECTED", **base,
                                          outputs={"action_id": action_id, "path": path,
                                                   "source": provenance[path].source}))
    return profile_payload(session, record)


async def cancel_extraction(*, repo: Repository, action_id: str, user_id: str) -> dict[str, Any]:
    action = await repo.get_pending_action(action_id, user_id)
    session = await repo.get_session(action.session_id, user_id)
    await repo.set_action_status(action, "CANCELLED")
    session, _ = await refresh_status(repo, session)
    return {"status": "CANCELLED", "profile_status": session.profile_status}


async def load_demo_profile(
    *, repo: Repository, session: SessionRecord, demo_id: str, request_id: str,
) -> dict[str, Any]:
    demo = await repo.get_demo_profile(demo_id)
    fields = flatten_profile(demo.profile)
    profile, _ = build_profile(fields)
    await repo.save_profile(ProfileRecord(
        session_id=session.session_id, user_id=session.user_id, status="EXTRACTED", confirmed_at=utcnow(),
        confirmed_fields=fields, profile=profile, field_provenance=demo.field_provenance,
    ))
    session, record = await refresh_status(repo, session)
    await repo.write_audit(AuditEvent(event="EXTRACTION_CONFIRMED", request_id=request_id,
                                      session_id=session.session_id, user_id=session.user_id,
                                      outcome="DEMO_PROFILE", outputs={"demo_id": demo_id}))
    return {"demo_id": demo_id, "display_name": demo.display_name, **profile_payload(session, record)}


async def record_stated_figures(
    *, repo: Repository, session: SessionRecord, figures: dict[str, Any], request_id: str,
) -> tuple[SessionRecord, ProfileRecord | None, list[str]]:
    """Figures the user asserts in chat (spec 16): stored as user_stated, so every later result is labelled unverified.

    Uses the same allowlist and validators as review corrections, so nothing outside the profile schema gets in.
    """
    record = await repo.get_profile(session.session_id, session.user_id)
    fields, provenance, recorded = apply_confirmation(
        record.confirmed_fields if record else {}, record.field_provenance if record else {}, {}, "user_stated",
        figures)
    profile, missing = build_profile(fields)
    await repo.save_profile(ProfileRecord(
        session_id=session.session_id, user_id=session.user_id, status=record.status if record else "EXTRACTED",
        confirmed_at=utcnow(), confirmed_fields=fields, profile=profile, field_provenance=provenance))
    session, record = await refresh_status(repo, session)
    for path in recorded:
        await repo.write_audit(AuditEvent(event="EXTRACTION_CORRECTED", request_id=request_id,
                                          session_id=session.session_id, user_id=session.user_id,
                                          outcome="USER_STATED", outputs={"path": path, "source": "user_stated"}))
    return session, record, missing
