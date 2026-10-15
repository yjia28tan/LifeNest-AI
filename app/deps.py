"""Request-scoped dependencies: services container, authenticated RequestContext, owned-session loader."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends, Path, Request

from app.auth import InvalidTokenError, TokenVerifier
from app.config import Settings
from app.engine.rules import Rules
from app.errors import ApiError
from app.kb.store import KnowledgeBase
from app.llm.extractor import DocumentExtractor
from app.llm.gateway import LLMGateway
from app.observability import get_logger
from app.storage.documents import DocumentStore
from app.storage.models import SessionRecord
from app.storage.repository import AuthzDeniedError, Repository

if TYPE_CHECKING:
    from app.ratelimit import SlidingWindowLimiter

log = get_logger("lifenest.auth")
_UNAUTHENTICATED = ApiError(401, "UNAUTHENTICATED", "a valid sign-in token is required",
                            headers={"WWW-Authenticate": "Bearer"})


@dataclass
class Services:
    settings: Settings
    rules: Rules
    repo: Repository
    documents: DocumentStore
    auth: TokenVerifier
    extractor: DocumentExtractor
    llm: LLMGateway
    kb: KnowledgeBase | None
    limiter: SlidingWindowLimiter


@dataclass(frozen=True)
class RequestContext:
    user_id: str
    request_id: str


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


async def require_user(request: Request, services: Services = Depends(get_services)) -> RequestContext:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        log.info("auth_rejected", reason="missing_bearer_token")
        raise _UNAUTHENTICATED
    try:
        user_id = await services.auth.verify(token.strip())
    except InvalidTokenError as exc:
        log.info("auth_rejected", reason=str(exc), cause=str(exc.__cause__)[:120] if exc.__cause__ else None,
                 verifier=type(services.auth).__name__)
        raise _UNAUTHENTICATED from None
    log.debug("auth_ok", verifier=type(services.auth).__name__)
    return RequestContext(user_id=user_id, request_id=request.state.request_id)


async def owned_session(
    session_id: str = Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$"),
    ctx: RequestContext = Depends(require_user),
    services: Services = Depends(get_services),
) -> SessionRecord:
    try:
        return await services.repo.get_session(session_id, ctx.user_id)
    except AuthzDeniedError:
        log.warning("AUTHZ_DENIED", resource="session")
        raise
