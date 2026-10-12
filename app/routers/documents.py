"""Document upload and extraction. Extraction only *proposes* fields; nothing is computed here."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel

from app.agent.review import propose_extraction
from app.deps import RequestContext, Services, get_services, owned_session, require_user
from app.errors import ApiError
from app.storage.documents import InvalidDocumentError, LocalDocumentStore
from app.storage.models import AuditEvent, DocumentKind, DocumentRecord, SessionRecord, utcnow
from app.storage.repository import new_id

router = APIRouter(prefix="/sessions/{session_id}/documents", tags=["documents"])
local_router = APIRouter(tags=["documents"])

_SAFE_ID = r"^[A-Za-z0-9_-]+$"
UploadContentType = Literal["image/png", "image/jpeg", "image/webp", "application/pdf"]


def _id_path() -> Any:
    # A fresh Path() per parameter: FastAPI mutates FieldInfo, so sharing one aliases both params.
    return Path(min_length=1, max_length=64, pattern=_SAFE_ID)


class UploadUrlRequest(BaseModel):
    kind: DocumentKind
    content_type: UploadContentType


@router.post("/upload-url", status_code=201)
async def create_upload_url(
    body: UploadUrlRequest,
    session: SessionRecord = Depends(owned_session),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    settings = services.settings
    doc_id = new_id("doc")
    url_ttl = timedelta(seconds=settings.upload_url_ttl_seconds)
    upload_url = await services.documents.create_upload_url(session.session_id, doc_id, body.content_type, url_ttl)
    await services.repo.save_document(DocumentRecord(
        doc_id=doc_id, session_id=session.session_id, kind=body.kind, content_type=body.content_type,
        gcs_uri=services.documents.uri(session.session_id, doc_id),
        expires_at=utcnow() + timedelta(hours=settings.document_ttl_hours),
    ))
    return {"doc_id": doc_id, "upload_url": upload_url, "method": "PUT", "content_type": body.content_type,
            "expires_at": utcnow() + url_ttl}


@router.post("/{doc_id}/extract")
async def extract_document(
    doc_id: str = _id_path(),
    session: SessionRecord = Depends(owned_session),
    ctx: RequestContext = Depends(require_user),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    doc = await services.repo.get_document(session.session_id, doc_id)
    return await propose_extraction(
        repo=services.repo, documents=services.documents, extractor=services.extractor,
        settings=services.settings, session=session, doc=doc, request_id=ctx.request_id,
    )


@local_router.put("/local-uploads/{session_id}/{doc_id}", status_code=204)
async def local_upload(
    request: Request,
    session_id: str = _id_path(),
    doc_id: str = _id_path(),
    ct: str = Query(max_length=40),
    exp: int = Query(),
    sig: str = Query(min_length=64, max_length=64),
    services: Services = Depends(get_services),
) -> None:
    """Local stand-in for a GCS signed PUT URL. The signature is the credential, exactly as with GCS."""
    store = services.documents
    if not isinstance(store, LocalDocumentStore):
        raise ApiError(404, "NOT_FOUND", "not found")
    try:
        store.verify_upload(session_id, doc_id, ct, exp, sig)
    except InvalidDocumentError as exc:
        raise ApiError(403, "FORBIDDEN", str(exc)) from None
    if request.headers.get("content-type", "").split(";")[0].strip() != ct:
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must match the upload link")

    limit = services.settings.max_document_bytes
    if int(request.headers.get("content-length") or 0) > limit:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "document is too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "document is too large")
        chunks.append(chunk)

    doc = await services.repo.get_document(session_id, doc_id)
    if doc.content_type != ct:
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Content-Type must match the upload link")
    try:
        await store.write(session_id, doc_id, b"".join(chunks), ct)
    except InvalidDocumentError as exc:
        raise ApiError(422, "DOCUMENT_UNREADABLE", str(exc)) from None
    await services.repo.save_document(doc.model_copy(update={"extraction_status": "UPLOADED", "uploaded_at": utcnow()}))
    await services.repo.write_audit(AuditEvent(event="DOCUMENT_UPLOADED", request_id=request.state.request_id,
                                               session_id=session_id, outputs={"doc_id": doc_id, "kind": doc.kind}))
