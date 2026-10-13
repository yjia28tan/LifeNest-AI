"""Backend selection. Every implementation is chosen by environment variable; no code changes between dev and prod."""

from __future__ import annotations

import os

from app.config import Settings
from app.storage.docdb import DocDB
from app.storage.documents import DocumentStore, GCSDocumentStore, LocalDocumentStore
from app.storage.memory import MemoryDocDB
from app.storage.repository import Repository

EMULATOR_PROJECT = "lifenest-local"
_NO_FIRESTORE = (
    "DB_BACKEND=firestore but no Firestore emulator or Google credentials were found. Either: "
    "(1) start the emulator (docker compose up -d firestore) and set FIRESTORE_EMULATOR_HOST=localhost:8080; "
    "(2) run `gcloud auth application-default login` and set GOOGLE_CLOUD_PROJECT; or "
    "(3) set DB_BACKEND=memory for a quick local run (data is lost on restart)."
)


def build_docdb(settings: Settings) -> DocDB:
    if settings.db_backend == "memory":
        return MemoryDocDB()
    from google.auth.exceptions import DefaultCredentialsError

    from app.storage.firestore import FirestoreDocDB

    project = settings.google_cloud_project
    if settings.firestore_emulator_host:
        # The client library only reads the emulator host from the process environment.
        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", settings.firestore_emulator_host)
    if os.environ.get("FIRESTORE_EMULATOR_HOST"):
        project = project or EMULATOR_PROJECT
    try:
        return FirestoreDocDB(project, settings.firestore_database)
    except DefaultCredentialsError:
        raise RuntimeError(_NO_FIRESTORE) from None


def build_repository(settings: Settings) -> Repository:
    return Repository(build_docdb(settings))


def build_document_store(settings: Settings) -> DocumentStore:
    if settings.storage_backend == "gcs":
        if not settings.gcs_bucket:
            raise ValueError("STORAGE_BACKEND=gcs requires GCS_BUCKET")
        return GCSDocumentStore(settings.gcs_bucket, settings.google_cloud_project, settings.max_document_bytes)
    return LocalDocumentStore(
        settings.local_upload_dir,
        settings.local_upload_secret.get_secret_value(),
        settings.public_base_url,
        settings.max_document_bytes,
    )
