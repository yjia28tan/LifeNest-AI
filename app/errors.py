"""Uniform error body: {"error": {"code", "message", "request_id"}} (spec section 13). Internals never leak."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability import get_logger
from app.storage.repository import ActionExpiredError, ActionNotPendingError, NotFoundError

log = get_logger("lifenest.errors")

_STATUS_CODES = {
    400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 410: "GONE", 413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED",
    500: "INTERNAL_ERROR", 504: "UPSTREAM_TIMEOUT",
}
_NOT_FOUND_CODES = {"session": "SESSION_NOT_FOUND", "action": "ACTION_NOT_FOUND"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers


def _body(request: Request, code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}}


def error_response(request: Request, status: int, code: str, message: str,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(_body(request, code, message), status_code=status, headers=headers)


def _validation_message(exc: RequestValidationError) -> str:
    # Field location and rule only; never echo the submitted value back.
    parts = []
    for err in exc.errors()[:3]:
        field = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path"))
        parts.append(f"{field}: {err.get('msg', 'invalid')}" if field else str(err.get("msg", "invalid")))
    return "; ".join(parts) or "invalid request"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "VALIDATION_ERROR", _validation_message(exc))

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        # AuthzDeniedError is a subclass: same status, code and message as genuinely missing.
        code = _NOT_FOUND_CODES.get(exc.what, "NOT_FOUND")
        return error_response(request, 404, code, f"{exc.what.replace('_', ' ')} not found")

    @app.exception_handler(ActionExpiredError)
    async def _expired(request: Request, exc: ActionExpiredError) -> JSONResponse:
        return error_response(request, 410, "ACTION_EXPIRED", "this action has expired; please start again")

    @app.exception_handler(ActionNotPendingError)
    async def _not_pending(request: Request, exc: ActionNotPendingError) -> JSONResponse:
        return error_response(request, 410, "ACTION_EXPIRED", "this action was already used or cancelled")

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code.lower().replace("_", " ")
        return error_response(request, exc.status_code, code, message, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", error_type=type(exc).__name__)
        return error_response(request, 500, "INTERNAL_ERROR", "something went wrong on our side; please try again")
