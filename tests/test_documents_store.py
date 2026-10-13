from __future__ import annotations

import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.storage.documents import (
    DocumentNotFoundError,
    InvalidDocumentError,
    LocalDocumentStore,
    validate_document,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64


@pytest.fixture
def store(tmp_path) -> LocalDocumentStore:
    return LocalDocumentStore(tmp_path, "test-secret", "http://localhost:8000/", max_bytes=1024)


def _params(url: str) -> tuple[str, dict[str, str]]:
    parsed = urlparse(url)
    return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}


async def test_signed_upload_url_round_trip(store) -> None:
    url = await store.create_upload_url("ses_1", "doc_1", "image/png", timedelta(minutes=10))
    path, q = _params(url)
    assert url.startswith("http://localhost:8000/local-uploads/ses_1/doc_1?")
    store.verify_upload("ses_1", "doc_1", q["ct"], int(q["exp"]), q["sig"])

    with pytest.raises(InvalidDocumentError):
        store.verify_upload("ses_1", "doc_2", q["ct"], int(q["exp"]), q["sig"])
    with pytest.raises(InvalidDocumentError):
        store.verify_upload("ses_1", "doc_1", "image/jpeg", int(q["exp"]), q["sig"])
    with pytest.raises(InvalidDocumentError):
        store.verify_upload("ses_1", "doc_1", q["ct"], int(q["exp"]), "0" * 64)


async def test_expired_upload_url_rejected(store) -> None:
    url = await store.create_upload_url("ses_1", "doc_1", "image/png", timedelta(seconds=-5))
    _, q = _params(url)
    assert int(q["exp"]) < time.time()
    with pytest.raises(InvalidDocumentError):
        store.verify_upload("ses_1", "doc_1", q["ct"], int(q["exp"]), q["sig"])


async def test_unsupported_content_type_has_no_upload_url(store) -> None:
    with pytest.raises(InvalidDocumentError):
        await store.create_upload_url("ses_1", "doc_1", "text/html", timedelta(minutes=10))


@pytest.mark.parametrize("bad_id", ["../etc", "a/b", "", "x" * 65, "doc 1"])
async def test_path_traversal_ids_rejected(store, bad_id) -> None:
    with pytest.raises(InvalidDocumentError):
        await store.write("ses_1", bad_id, PNG, "image/png")


async def test_write_read_purge(store, tmp_path) -> None:
    await store.write("ses_1", "doc_1", PNG, "image/png")
    await store.write("ses_1", "doc_2", JPEG, "image/jpeg")
    assert await store.read("ses_1", "doc_1") == PNG
    assert store.uri("ses_1", "doc_1") == "local://ses_1/doc_1"

    assert await store.purge_session("ses_1") == 2
    assert not (tmp_path / "ses_1").exists()
    with pytest.raises(DocumentNotFoundError):
        await store.read("ses_1", "doc_1")
    assert await store.purge_session("ses_1") == 0


@pytest.mark.parametrize(
    ("data", "content_type"),
    [(PNG, "image/jpeg"), (b"<html>", "image/png"), (b"", "image/png"), (b"RIFF\x00\x00\x00\x00AVI " + b"0" * 8, "image/webp")],
)
def test_content_must_match_declared_type(data, content_type) -> None:
    with pytest.raises(InvalidDocumentError):
        validate_document(data, content_type, max_bytes=1024)


def test_size_limit_and_valid_types() -> None:
    validate_document(WEBP, "image/webp", max_bytes=1024)
    validate_document(b"%PDF-1.7" + b"0" * 8, "application/pdf", max_bytes=1024)
    with pytest.raises(InvalidDocumentError, match="too large"):
        validate_document(PNG + b"\x00" * 2048, "image/png", max_bytes=1024)
