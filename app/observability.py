"""Structured JSON logging for Cloud Logging, and the per-request id carried through every log line."""

from __future__ import annotations

import logging
import re
import secrets
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
_SEVERITY = {"debug": "DEBUG", "info": "INFO", "warning": "WARNING", "error": "ERROR", "critical": "CRITICAL"}


def new_request_id() -> str:
    return f"req_{secrets.token_hex(8)}"


def _cloud_severity(_: Any, method: str, event: dict[str, Any]) -> dict[str, Any]:
    event["severity"] = _SEVERITY.get(method, "DEFAULT")
    return event


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _cloud_severity,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "lifenest") -> Any:
    return structlog.get_logger(name)


def route_template(request: Request) -> str:
    """Log the route pattern, not the concrete path, so ids and query strings never reach logs."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or re.sub(r"/[^/]+$", "/…", request.url.path)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, binds it to every log line, and logs one access line. Never logs headers or bodies."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self.log = get_logger("lifenest.http")

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = new_request_id()
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            self.log.error("request_failed", method=request.method, route=route_template(request),
                           error_type=type(exc).__name__,
                           latency_ms=round((time.perf_counter() - start) * 1000))
            raise
        response.headers[REQUEST_ID_HEADER] = request_id
        self.log.info("request", method=request.method, route=route_template(request),
                      status=response.status_code, latency_ms=round((time.perf_counter() - start) * 1000))
        return response
