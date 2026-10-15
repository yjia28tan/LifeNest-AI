"""Runtime settings from environment variables (spec section 19). No defaults point at real cloud resources."""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # .env holds non-secret config; secrets.env (also gitignored) holds secrets and wins on conflicts.
    model_config = SettingsConfigDict(env_file=(".env", "secrets.env"), env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    timezone: str = "Asia/Kuala_Lumpur"

    google_cloud_project: str = ""
    google_cloud_location: str = "asia-southeast1"
    # Local dev only: path to a service-account key kept OUTSIDE the repo. Cloud Run uses its attached identity.
    google_application_credentials: str = ""

    # firebase = verify Firebase ID tokens; dev = "Bearer dev:<user_id>", refused when APP_ENV=prod.
    auth_backend: Literal["firebase", "dev"] = "dev"
    firebase_project_id: str = ""
    cors_allowed_origins: list[str] = ["http://localhost:5000", "http://localhost:5173"]

    kb_backend: Literal["firestore", "memory"] = "memory"
    storage_backend: Literal["gcs", "local"] = "local"
    llm_backend: Literal["vertex", "fake"] = "fake"
    # Not in spec section 19. memory = zero-setup local run; firestore = emulator or real project (required in prod).
    db_backend: Literal["firestore", "memory"] = "memory"

    firestore_emulator_host: str = ""
    firestore_database: str = "(default)"

    gcs_bucket: str = ""
    document_ttl_hours: int = 24
    session_ttl_hours: int = 24
    max_document_bytes: int = 10 * 1024 * 1024
    upload_url_ttl_seconds: int = 900
    # Extracted fields below this confidence are flagged for the user to check in the review panel.
    extraction_min_confidence: float = 0.7
    mock_documents_dir: Path = ROOT / "data" / "mock_documents"
    local_upload_dir: Path = ROOT / ".local_uploads"
    local_upload_secret: SecretStr = Field(default_factory=lambda: SecretStr(secrets.token_urlsafe(32)))
    public_base_url: str = "http://localhost:8000"

    gemini_model: str = ""
    embedding_model: str = ""
    embedding_dim: int = 768
    llm_timeout_seconds: float = 30

    rules_config_path: Path = ROOT / "config" / "rules_my_2026_07.yaml"
    kb_embeddings_path: Path = ROOT / "data" / "kb_embeddings.json"
    knowledge_dir: Path = ROOT / "knowledge"
    property_stock_path: Path = ROOT / "data" / "property_stock.json"
    demo_profiles_path: Path = ROOT / "data" / "demo_profiles.json"

    agent_max_steps: int = 6
    tool_timeout_seconds: float = 8
    turn_timeout_seconds: float = 60
    # Unset = the embedder's measured threshold (0.55 for Vertex text-embedding-005, 0.15 offline).
    kb_min_score: float | None = None
    rate_limit_per_minute: int = 20
    pending_action_ttl_seconds: int = 900
    grounding_check: Literal["warn", "enforce"] = "warn"

    def export_process_env(self) -> None:
        """Google client libraries read credentials from the process environment, not from .env files."""
        if self.google_application_credentials:
            path = Path(os.path.expandvars(self.google_application_credentials)).expanduser()
            if not path.is_file():
                raise RuntimeError(f"GOOGLE_APPLICATION_CREDENTIALS points to a missing file: {path}")
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(path))
        if self.google_cloud_project:
            os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.google_cloud_project)


@lru_cache
def get_settings() -> Settings:
    return Settings()
