"""Logging: readable console lines locally, structured JSON for Cloud Logging, and a request id on every line.

Log lines carry step names, kinds, counts, status codes and latency - never session/user ids, tokens or amounts.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import secrets
import sys
import time
import traceback
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


def _safe_exception(_: Any, __: str, event: dict[str, Any]) -> dict[str, Any]:
    """Stack location (file, line, function) and type only: exception messages can echo user figures."""
    exc_info = event.pop("exc_info", None)
    if exc_info is True:
        exc_info = sys.exc_info()
    if isinstance(exc_info, BaseException):
        exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
    if not exc_info or exc_info[0] is None:
        return event
    frames = traceback.extract_tb(exc_info[2])
    event["error_type"] = exc_info[0].__name__
    event["stack"] = [f"{f.filename}:{f.lineno} in {f.name}" for f in frames[-12:]]
    if frames:
        event["error_at"] = event["stack"][-1]
    causes, cur = [], exc_info[1]
    while cur is not None and (cur := cur.__cause__) is not None and len(causes) < 5:
        tb = traceback.extract_tb(cur.__traceback__)
        causes.append(f"{type(cur).__name__} at {tb[-1].filename}:{tb[-1].lineno}" if tb else type(cur).__name__)
    if causes:
        event["caused_by"] = causes
    return event


def _console_renderer() -> Any:
    colors = sys.stdout.isatty() and (sys.platform != "win32" or importlib.util.find_spec("colorama") is not None)
    return structlog.dev.ConsoleRenderer(colors=colors, pad_event_to=28,
                                         exception_formatter=structlog.dev.plain_traceback)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """fmt: "console" = one readable line per event with full tracebacks; "json" = Cloud Logging entries."""
    shared: list[Any] = [structlog.contextvars.merge_contextvars, structlog.processors.add_log_level]
    if fmt == "console":
        processors = [*shared, structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
                      _console_renderer()]
    else:
        processors = [*shared, _cloud_severity, _safe_exception,
                      structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
                      structlog.processors.EventRenamer("message"), structlog.processors.JSONRenderer()]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "lifenest") -> Any:
    # Initial values keep the proxy lazy, so module-level loggers pick up configure_logging() made later.
    return structlog.get_logger(logger_name=name)


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
        except Exception:
            self.log.error("request_failed", method=request.method, route=route_template(request),
                           latency_ms=round((time.perf_counter() - start) * 1000), exc_info=True)
            raise
        response.headers[REQUEST_ID_HEADER] = request_id
        latency_ms = round((time.perf_counter() - start) * 1000)
        level = "error" if response.status_code >= 500 else "warning" if response.status_code >= 400 else "info"
        getattr(self.log, level)("request", method=request.method, route=route_template(request),
                                 status=response.status_code, latency_ms=latency_ms)
        return response
