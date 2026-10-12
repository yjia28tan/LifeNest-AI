from __future__ import annotations

import os

import pytest
from google.auth.exceptions import DefaultCredentialsError

from app.config import Settings
from app.main import create_app
from app.storage import EMULATOR_PROJECT, build_docdb
from app.storage.memory import MemoryDocDB


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    # build_docdb writes FIRESTORE_EMULATOR_HOST into os.environ; keep that out of other tests.
    env = {k: v for k, v in os.environ.items() if k != "FIRESTORE_EMULATOR_HOST"}
    monkeypatch.setattr(os, "environ", env)


def test_default_runs_with_zero_setup() -> None:
    assert isinstance(build_docdb(_settings()), MemoryDocDB)


def test_missing_firestore_credentials_gives_actionable_error(monkeypatch) -> None:
    def no_credentials(self, project, database):
        raise DefaultCredentialsError("no ADC")

    monkeypatch.setattr("app.storage.firestore.FirestoreDocDB.__init__", no_credentials)
    with pytest.raises(RuntimeError, match="DB_BACKEND=memory") as exc:
        build_docdb(_settings(db_backend="firestore"))
    assert "FIRESTORE_EMULATOR_HOST" in str(exc.value)


def test_emulator_host_from_settings_reaches_the_client(monkeypatch) -> None:
    seen: dict[str, object] = {}

    class FakeFirestore:
        def __init__(self, project, database):
            seen.update(project=project, env=os.environ.get("FIRESTORE_EMULATOR_HOST"))

    monkeypatch.setattr("app.storage.firestore.FirestoreDocDB", FakeFirestore)
    build_docdb(_settings(db_backend="firestore", firestore_emulator_host="localhost:8080"))
    assert seen == {"project": EMULATOR_PROJECT, "env": "localhost:8080"}


def test_memory_db_refused_in_production(tmp_path) -> None:
    settings = _settings(app_env="prod", auth_backend="firebase", firebase_project_id="p",
                         local_upload_dir=tmp_path)
    with pytest.raises(RuntimeError, match="DB_BACKEND=memory"):
        create_app(settings)
