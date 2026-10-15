"""Session-scoped document storage. Bytes live here only until the session is purged or the TTL expires."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from app.observability import get_logger

log = get_logger("lifenest.storage")

ALLOWED_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "application/pdf"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAGIC: dict[str, tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
    "application/pdf": (b"%PDF",),
}


class DocumentNotFoundError(Exception):
    pass


class InvalidDocumentError(ValueError):
    pass


def check_ids(*ids: str) -> None:
    for value in ids:
        if not _SAFE_ID.match(value):
            raise InvalidDocumentError("invalid identifier")


def validate_document(data: bytes, content_type: str, max_bytes: int) -> None:
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise InvalidDocumentError(f"unsupported content type; allowed: {sorted(ALLOWED_CONTENT_TYPES)}")
    if not data:
        raise InvalidDocumentError("empty document")
    if len(data) > max_bytes:
        raise InvalidDocumentError("document too large")
    if not data.startswith(_MAGIC[content_type]) or (content_type == "image/webp" and data[8:12] != b"WEBP"):
        raise InvalidDocumentError("file contents do not match the declared content type")


class DocumentStore(Protocol):
    backend: str

    def uri(self, session_id: str, doc_id: str) -> str: ...
    async def create_upload_url(self, session_id: str, doc_id: str, content_type: str, ttl: timedelta) -> str: ...
    async def read(self, session_id: str, doc_id: str) -> bytes: ...
    async def purge_session(self, session_id: str) -> int: ...
    async def ping(self) -> bool: ...


class LocalDocumentStore:
    """Writes to ./.local_uploads/{session_id}/{doc_id}. Upload URLs are HMAC-signed and short-lived."""

    backend = "local"

    def __init__(self, root: Path, secret: str, public_base_url: str, max_bytes: int) -> None:
        self.root = root
        self._secret = secret.encode()
        self.public_base_url = public_base_url.rstrip("/")
        self.max_bytes = max_bytes

    def _path(self, session_id: str, doc_id: str) -> Path:
        check_ids(session_id, doc_id)
        return self.root / session_id / doc_id

    def _sign(self, session_id: str, doc_id: str, content_type: str, exp: int) -> str:
        msg = f"{session_id}\n{doc_id}\n{content_type}\n{exp}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def uri(self, session_id: str, doc_id: str) -> str:
        check_ids(session_id, doc_id)
        return f"local://{session_id}/{doc_id}"

    async def create_upload_url(self, session_id: str, doc_id: str, content_type: str, ttl: timedelta) -> str:
        check_ids(session_id, doc_id)
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise InvalidDocumentError("unsupported content type")
        exp = int(time.time() + ttl.total_seconds())
        query = urlencode({"ct": content_type, "exp": exp, "sig": self._sign(session_id, doc_id, content_type, exp)})
        return f"{self.public_base_url}/local-uploads/{session_id}/{doc_id}?{query}"

    def verify_upload(self, session_id: str, doc_id: str, content_type: str, exp: int, sig: str) -> None:
        check_ids(session_id, doc_id)
        expected = self._sign(session_id, doc_id, content_type, exp)
        if not hmac.compare_digest(expected, sig) or exp < time.time():
            raise InvalidDocumentError("upload link is invalid or has expired")

    async def write(self, session_id: str, doc_id: str, data: bytes, content_type: str) -> None:
        validate_document(data, content_type, self.max_bytes)
        path = self._path(session_id, doc_id)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        await asyncio.to_thread(_write)

    async def read(self, session_id: str, doc_id: str) -> bytes:
        path = self._path(session_id, doc_id)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise DocumentNotFoundError(doc_id) from exc

    async def purge_session(self, session_id: str) -> int:
        check_ids(session_id)
        folder = self.root / session_id

        def _purge() -> int:
            if not folder.exists():
                return 0
            count = 0
            for p in folder.iterdir():
                if p.is_file():
                    p.unlink()
                    count += 1
                else:
                    shutil.rmtree(p)
            # Sync clients (e.g. OneDrive) can briefly lock an emptied dir on Windows; an empty dir holds no data.
            for attempt in range(5):
                try:
                    folder.rmdir()
                    break
                except FileNotFoundError:
                    break
                except PermissionError:
                    if attempt < 4:
                        time.sleep(0.1 * (attempt + 1))
                    else:
                        log.warning("local_purge.empty_folder_locked", files_deleted=count)
            return count

        return await asyncio.to_thread(_purge)

    async def ping(self) -> bool:
        return True


class GCSDocumentStore:
    """Cloud Storage under gs://{bucket}/sessions/{sid}/{doc_id}. The bucket carries a 24h lifecycle delete rule."""

    backend = "gcs"

    def __init__(self, bucket: str, project: str | None, max_bytes: int) -> None:
        from google.cloud import storage

        self.client = storage.Client(project=project or None)
        self.bucket = self.client.bucket(bucket)
        self.max_bytes = max_bytes

    @staticmethod
    def _name(session_id: str, doc_id: str) -> str:
        check_ids(session_id, doc_id)
        return f"sessions/{session_id}/{doc_id}"

    def uri(self, session_id: str, doc_id: str) -> str:
        return f"gs://{self.bucket.name}/{self._name(session_id, doc_id)}"

    def _signing_kwargs(self) -> dict[str, Any]:
        # On Cloud Run there is no private key; sign through IAM with the runtime service account.
        creds = self.client._credentials
        if hasattr(creds, "signer") and getattr(creds, "signer", None) is not None:
            return {}
        import google.auth.transport.requests

        signing_creds = creds.with_scopes(["https://www.googleapis.com/auth/cloud-platform"])
        signing_creds.refresh(google.auth.transport.requests.Request())
        return {"service_account_email": signing_creds.service_account_email, "access_token": signing_creds.token}

    async def create_upload_url(self, session_id: str, doc_id: str, content_type: str, ttl: timedelta) -> str:
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise InvalidDocumentError("unsupported content type")
        blob = self.bucket.blob(self._name(session_id, doc_id))
        return await asyncio.to_thread(
            blob.generate_signed_url, version="v4", expiration=ttl, method="PUT",
            content_type=content_type, **self._signing_kwargs(),
        )

    async def read(self, session_id: str, doc_id: str) -> bytes:
        from google.api_core.exceptions import NotFound

        blob = self.bucket.blob(self._name(session_id, doc_id))
        try:
            await asyncio.to_thread(blob.reload)
        except NotFound as exc:
            raise DocumentNotFoundError(doc_id) from exc
        if (blob.size or 0) > self.max_bytes:
            raise InvalidDocumentError("document too large")
        return await asyncio.to_thread(blob.download_as_bytes)

    async def purge_session(self, session_id: str) -> int:
        check_ids(session_id)

        def _purge() -> int:
            blobs = list(self.client.list_blobs(self.bucket, prefix=f"sessions/{session_id}/"))
            for blob in blobs:
                blob.delete()
            return len(blobs)

        return await asyncio.to_thread(_purge)

    async def ping(self) -> bool:
        try:
            def _list_one_blob() -> None:
                next(iter(self.client.list_blobs(self.bucket, max_results=1)), None)

            await asyncio.to_thread(_list_one_blob)
            return True
        except Exception:
            return False
