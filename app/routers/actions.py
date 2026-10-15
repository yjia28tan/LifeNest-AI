"""Confirm or cancel a pending extraction review. The only path from EXTRACTED to CONFIRMED."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from app.agent.review import cancel_extraction, confirm_extraction
from app.deps import RequestContext, Services, get_services, require_user
from app.observability import get_logger

log = get_logger("lifenest.actions")

router = APIRouter(prefix="/actions", tags=["actions"])


class ConfirmRequest(BaseModel):
    # Path -> corrected value, e.g. {"income.gross_monthly": 6200, "person.age": 29}. May be empty.
    corrections: dict[str, Any] = Field(default_factory=dict, max_length=30)


@router.post("/{action_id}/confirm")
async def confirm(
    body: ConfirmRequest | None = None,
    action_id: str = Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
    ctx: RequestContext = Depends(require_user),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    corrections = (body or ConfirmRequest()).corrections
    log.info("review.confirm.start", corrections=len(corrections))
    result = await confirm_extraction(repo=services.repo, action_id=action_id, user_id=ctx.user_id,
                                      corrections=corrections, request_id=ctx.request_id)
    log.info("review.confirm.done", profile_status=result["profile_status"],
             missing_fields=len(result["missing_fields"]))
    return result


@router.post("/{action_id}/cancel")
async def cancel(
    action_id: str = Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
    ctx: RequestContext = Depends(require_user),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    log.info("review.cancel.start")
    result = await cancel_extraction(repo=services.repo, action_id=action_id, user_id=ctx.user_id)
    log.info("review.cancel.done", profile_status=result["profile_status"])
    return result
