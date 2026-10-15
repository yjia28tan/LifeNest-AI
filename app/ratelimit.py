"""Per-user sliding-window rate limit (RATE_LIMIT_PER_MINUTE).

In-process by design: each Cloud Run instance enforces its own window, which bounds abuse per instance without adding
a datastore round-trip to every request. Set --max-instances to bound the global rate.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from fastapi import Depends, Request

from app.deps import RequestContext, Services, get_services, require_user
from app.errors import ApiError
from app.storage.models import AuditEvent


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> float | None:
        """Record a request; return seconds to wait if the key is over its limit, else None."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return hits[0] + self.window - now
            hits.append(now)
            return None


def rate_limited(bucket: str):  # noqa: ANN201 - FastAPI dependency factory
    async def dependency(
        request: Request,
        ctx: RequestContext = Depends(require_user),
        services: Services = Depends(get_services),
    ) -> None:
        wait = services.limiter.hit(f"{bucket}:{ctx.user_id}")
        if wait is None:
            return
        await services.repo.write_audit(AuditEvent(
            event="RATE_LIMITED", request_id=ctx.request_id, user_id=ctx.user_id,
            session_id=request.path_params.get("session_id"), outcome=bucket))
        raise ApiError(429, "RATE_LIMITED", "You're sending requests too quickly. Please wait a moment and try again.",
                       headers={"Retry-After": str(max(1, math.ceil(wait)))})

    return dependency
