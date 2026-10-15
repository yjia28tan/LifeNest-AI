"""Chat turns. The reply is prose; every figure the UI renders comes from `cards` and `computations`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.agent.orchestrator import run_turn
from app.deps import RequestContext, Services, get_services, owned_session, require_user
from app.ratelimit import rate_limited
from app.storage.models import SessionRecord

router = APIRouter(prefix="/sessions/{session_id}/messages", tags=["chat"])


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@router.post("", dependencies=[Depends(rate_limited("chat"))])
async def post_message(
    body: MessageRequest,
    session: SessionRecord = Depends(owned_session),
    ctx: RequestContext = Depends(require_user),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return await run_turn(services, ctx, session, body.message.strip() or body.message)


@router.get("")
async def list_messages(
    limit: int = Query(50, ge=1, le=100),
    session: SessionRecord = Depends(owned_session),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    messages = await services.repo.list_messages(session.session_id, limit=limit)
    return {"messages": [m.model_dump(mode="json") for m in messages]}
