"""Right to erasure: DELETE /sessions/{sid} purges documents, profile, messages and pending reviews."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.deps import RequestContext, Services, get_services, owned_session, require_user
from app.observability import get_logger
from app.storage.models import SessionRecord

log = get_logger("lifenest.privacy")

router = APIRouter(prefix="/sessions", tags=["privacy"])


@router.delete("/{session_id}")
async def purge_session(
    session: SessionRecord = Depends(owned_session),
    ctx: RequestContext = Depends(require_user),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    # Bytes first: if record deletion fails afterwards, no document content is left behind.
    log.info("purge.files.start", storage=services.documents.backend)
    files = await services.documents.purge_session(session.session_id)
    log.info("purge.files.done", files_deleted=files)
    log.info("purge.records.start", db=services.settings.db_backend)
    counts = await services.repo.purge_session(session, request_id=ctx.request_id)
    log.info("purge.records.done", **counts)
    return {"session_id": session.session_id, "status": "purged", "files_deleted": files, **counts}
