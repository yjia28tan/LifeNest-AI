"""Session lifecycle. A session is owned by the token's user and carries the rules version it was created under."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.agent.review import load_demo_profile
from app.deps import RequestContext, Services, get_services, owned_session, require_user
from app.storage.models import ProfileStatus, SessionRecord

router = APIRouter(prefix="/sessions", tags=["sessions"])

Locale = Literal["en", "ms", "zh"]


class CreateSessionRequest(BaseModel):
    # extra=ignore: a smuggled "user_id" in the body is dropped, never read.
    model_config = ConfigDict(extra="ignore")

    locale: Locale = "en"


class SessionResponse(BaseModel):
    session_id: str
    profile_status: ProfileStatus
    locale: str
    rules_version: str
    expires_at: datetime


def _response(rec: SessionRecord) -> SessionResponse:
    return SessionResponse(session_id=rec.session_id, profile_status=rec.profile_status, locale=rec.locale,
                           rules_version=rec.rules_version, expires_at=rec.expires_at)


@router.post("", status_code=201, response_model=SessionResponse)
async def create_session(
    body: CreateSessionRequest | None = None,
    ctx: RequestContext = Depends(require_user),
    services: Services = Depends(get_services),
) -> SessionResponse:
    rec = await services.repo.create_session(
        ctx.user_id,
        services.rules.version,
        timedelta(hours=services.settings.session_ttl_hours),
        locale=(body or CreateSessionRequest()).locale,
    )
    return _response(rec)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session: SessionRecord = Depends(owned_session)) -> SessionResponse:
    return _response(session)


class DemoProfileRequest(BaseModel):
    demo_id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_-]+$")


@router.post("/{session_id}/demo-profile")
async def use_demo_profile(
    body: DemoProfileRequest,
    session: SessionRecord = Depends(owned_session),
    ctx: RequestContext = Depends(require_user),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    """Load a pre-extracted, already-reviewed synthetic profile (spec 11.3) for a fast demo."""
    return await load_demo_profile(repo=services.repo, session=session, demo_id=body.demo_id,
                                   request_id=ctx.request_id)
