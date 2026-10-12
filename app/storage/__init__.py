"""Backend selection. Every implementation is chosen by environment variable; no code changes between dev and prod."""

from __future__ import annotations

from app.config import Settings
from app.storage.docdb import DocDB
from app.storage.documents import DocumentStore, GCSDocumentStore, LocalDocumentStore
from app.storage.memory import MemoryDocDB
from app.storage.repository import Repository


def build_docdb(settings: Settings) -> DocDB:
    if settings.db_backend == "memory":
        return MemoryDocDB()
    from app.storage.firestore import FirestoreDocDB

    return FirestoreDocDB(settings.google_cloud_project, settings.firestore_database)


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
