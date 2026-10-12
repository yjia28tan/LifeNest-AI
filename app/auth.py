"""ID-token verification. user_id comes only from a verified token — never from a body, query or the LLM."""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.request
from collections.abc import Callable
from typing import Protocol

from google.auth import exceptions as google_auth_exceptions
from google.auth import jwt

FIREBASE_CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_UID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_DEFAULT_CERT_TTL = 3600.0

CertFetcher = Callable[[], tuple[dict[str, str], float]]


class InvalidTokenError(Exception):
    pass


class TokenVerifier(Protocol):
    async def verify(self, token: str) -> str: ...


def _fetch_firebase_certs() -> tuple[dict[str, str], float]:
    with urllib.request.urlopen(FIREBASE_CERTS_URL, timeout=5) as resp:  # noqa: S310 - fixed https URL
        certs = json.loads(resp.read())
        match = re.search(r"max-age=(\d+)", resp.headers.get("Cache-Control", ""))
    return certs, float(match.group(1)) if match else _DEFAULT_CERT_TTL


class FirebaseTokenVerifier:
    """Verifies a Firebase Auth ID token: RS256 signature, aud, iss, exp/iat, and a sane uid."""

    def __init__(self, project_id: str, fetch_certs: CertFetcher = _fetch_firebase_certs) -> None:
        if not project_id:
            raise ValueError("AUTH_BACKEND=firebase requires FIREBASE_PROJECT_ID or GOOGLE_CLOUD_PROJECT")
        self.project_id = project_id
        self._fetch = fetch_certs
        self._certs: dict[str, str] = {}
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _get_certs(self) -> dict[str, str]:
        async with self._lock:
            if time.time() >= self._expires_at:
                self._certs, ttl = await asyncio.to_thread(self._fetch)
                self._expires_at = time.time() + ttl
            return self._certs

    async def verify(self, token: str) -> str:
        certs = await self._get_certs()
        try:
            claims = jwt.decode(token, certs=certs, audience=self.project_id, clock_skew_in_seconds=10)
        except (ValueError, google_auth_exceptions.GoogleAuthError) as exc:
            raise InvalidTokenError("token verification failed") from exc
        if claims.get("iss") != f"https://securetoken.google.com/{self.project_id}":
            raise InvalidTokenError("wrong issuer")
        uid = claims.get("sub")
        if not isinstance(uid, str) or not _UID.match(uid):
            raise InvalidTokenError("invalid subject")
        return uid


class DevTokenVerifier:
    """Local/test only: accepts "dev:<user_id>". create_app refuses this backend when APP_ENV=prod."""

    async def verify(self, token: str) -> str:
        prefix, _, uid = token.partition(":")
        if prefix != "dev" or not _UID.match(uid):
            raise InvalidTokenError("malformed dev token")
        return uid
