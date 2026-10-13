from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ALICE = {"Authorization": "Bearer dev:alice"}
BOB = {"Authorization": "Bearer dev:bob"}
REQ_ID = re.compile(r"^req_[0-9a-f]{16}$")


def make_settings(tmp_path, **overrides) -> Settings:
    defaults = dict(app_env="test", db_backend="memory", storage_backend="local", llm_backend="fake",
                    kb_backend="memory", auth_backend="dev", local_upload_dir=tmp_path / "uploads")
    return Settings(_env_file=None, **{**defaults, **overrides})


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(make_settings(tmp_path))) as c:
        yield c


def _assert_error(resp, status: int, code: str) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert REQ_ID.match(body["error"]["request_id"])
    return body["error"]


def test_health_is_public(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "firestore": "ok", "storage": "ok", "kb": "ok",
                           "llm": "fake", "rules_version": "my-2026-07"}
    assert REQ_ID.match(resp.headers["X-Request-ID"])


def test_request_ids_are_unique(client) -> None:
    ids = {client.get("/health").headers["X-Request-ID"] for _ in range(5)}
    assert len(ids) == 5


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer "}, {"Authorization": "Basic abc"},
                                     {"Authorization": "Bearer dev:"}, {"Authorization": "Bearer forged"}])
def test_sessions_require_a_valid_token(client, headers) -> None:
    err = _assert_error(client.post("/sessions", headers=headers), 401, "UNAUTHENTICATED")
    assert "token" in err["message"]


def test_create_and_read_own_session(client) -> None:
    resp = client.post("/sessions", headers=ALICE, json={"locale": "ms"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["profile_status"] == "EMPTY" and body["locale"] == "ms"
    assert body["rules_version"] == "my-2026-07"
    assert client.get(f"/sessions/{body['session_id']}", headers=ALICE).json()["session_id"] == body["session_id"]


def test_create_session_without_body(client) -> None:
    resp = client.post("/sessions", headers=ALICE)
    assert resp.status_code == 201 and resp.json()["locale"] == "en"


def test_other_users_session_is_indistinguishable_from_missing(client) -> None:
    sid = client.post("/sessions", headers=ALICE).json()["session_id"]
    foreign = _assert_error(client.get(f"/sessions/{sid}", headers=BOB), 404, "SESSION_NOT_FOUND")
    missing = _assert_error(client.get("/sessions/ses_nope", headers=BOB), 404, "SESSION_NOT_FOUND")
    assert foreign["message"] == missing["message"]

    repo = client.app.state.services.repo
    import asyncio
    denied = asyncio.run(repo.list_audit(session_id=sid, event="AUTHZ_DENIED"))
    assert [e.user_id for e in denied] == ["bob"]


def test_user_id_in_body_is_ignored(client) -> None:
    sid = client.post("/sessions", headers=ALICE, json={"user_id": "bob"}).json()["session_id"]
    assert client.get(f"/sessions/{sid}", headers=ALICE).status_code == 200
    _assert_error(client.get(f"/sessions/{sid}", headers=BOB), 404, "SESSION_NOT_FOUND")


def test_validation_errors_use_the_error_format(client) -> None:
    err = _assert_error(client.post("/sessions", headers=ALICE, json={"locale": "fr"}), 422, "VALIDATION_ERROR")
    assert "locale" in err["message"] and "fr" not in err["message"]
    _assert_error(client.get("/sessions/bad id!", headers=ALICE), 422, "VALIDATION_ERROR")


def test_unknown_route_uses_the_error_format(client) -> None:
    _assert_error(client.get("/nope", headers=ALICE), 404, "NOT_FOUND")


def test_unhandled_errors_do_not_leak(tmp_path) -> None:
    app = create_app(make_settings(tmp_path))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail RM6200")

    with TestClient(app, raise_server_exceptions=False) as c:
        err = _assert_error(c.get("/boom"), 500, "INTERNAL_ERROR")
    assert "secret" not in err["message"] and "6200" not in err["message"]


def test_access_log_is_json_without_secrets(client, capsys) -> None:
    capsys.readouterr()
    sid = client.post("/sessions", headers={"Authorization": "Bearer dev:alice"}).json()["session_id"]
    client.get(f"/sessions/{sid}", headers=ALICE)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    access = [line for line in lines if line.get("message") == "request"]
    assert {a["route"] for a in access} >= {"/sessions", "/sessions/{session_id}"}
    for line in access:
        assert REQ_ID.match(line["request_id"]) and line["severity"] == "INFO"
    raw = json.dumps(lines)
    assert "dev:alice" not in raw and sid not in raw and "Authorization" not in raw


def test_dev_auth_refused_in_production(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="AUTH_BACKEND=dev"):
        create_app(make_settings(tmp_path, app_env="prod"))


def test_docs_hidden_in_production(tmp_path, monkeypatch) -> None:
    from app.storage.memory import MemoryDocDB

    class FakeFirestore(MemoryDocDB):
        def __init__(self, project, database):
            super().__init__()

    monkeypatch.setattr("app.storage.firestore.FirestoreDocDB", FakeFirestore)
    settings = make_settings(tmp_path, app_env="prod", auth_backend="firebase", firebase_project_id="p",
                             db_backend="firestore")
    with TestClient(create_app(settings)) as c:
        assert c.get("/docs").status_code == 404


def test_health_reports_missing_kb(tmp_path) -> None:
    settings = make_settings(tmp_path, kb_embeddings_path=tmp_path / "missing.json")
    with TestClient(create_app(settings)) as c:
        body = c.get("/health").json()
    assert body["kb"] == "unavailable" and body["status"] == "degraded"
