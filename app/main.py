"""FastAPI entry point: `uvicorn app.main:app`. Backends are wired from settings in the lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import DevTokenVerifier, FirebaseTokenVerifier, TokenVerifier
from app.config import Settings, get_settings
from app.deps import Services
from app.engine.rules import load_rules
from app.errors import install_error_handlers
from app.kb import build_knowledge_base
from app.llm.extractor import DocumentExtractor, GeminiExtractor
from app.llm.fake import FakeExtractor
from app.observability import RequestContextMiddleware, configure_logging, get_logger
from app.routers import actions, documents, sessions
from app.storage import build_document_store, build_repository
from app.storage.documents import LocalDocumentStore
from app.storage.seed_data import load_demo_profiles, load_property_stock

log = get_logger("lifenest.app")


def build_auth(settings: Settings) -> TokenVerifier:
    if settings.auth_backend == "dev":
        if settings.app_env == "prod":
            raise RuntimeError("AUTH_BACKEND=dev is not allowed when APP_ENV=prod")
        return DevTokenVerifier()
    return FirebaseTokenVerifier(settings.firebase_project_id or settings.google_cloud_project)


def build_extractor(settings: Settings) -> DocumentExtractor:
    if settings.llm_backend == "vertex":
        return GeminiExtractor(settings.google_cloud_project, settings.google_cloud_location,
                               settings.gemini_model, settings.llm_timeout_seconds)
    return FakeExtractor(settings.mock_documents_dir)


def build_services(settings: Settings) -> Services:
    auth = build_auth(settings)
    if settings.app_env == "prod" and settings.db_backend == "memory":
        raise RuntimeError("DB_BACKEND=memory is not allowed when APP_ENV=prod; use firestore")
    try:
        kb = build_knowledge_base(settings)
    except (FileNotFoundError, ValueError) as exc:
        # The API still serves; search_regulation reports the KB as unavailable.
        log.warning("kb_unavailable", error_type=type(exc).__name__)
        kb = None
    return Services(
        settings=settings,
        rules=load_rules(settings.rules_config_path),
        repo=build_repository(settings),
        documents=build_document_store(settings),
        auth=auth,
        extractor=build_extractor(settings),
        kb=kb,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    # Fail fast on misconfiguration (bad rules file, dev auth in prod) before serving traffic.
    services = build_services(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.db_backend == "memory":
            # Nothing persists in memory mode, so reference data is seeded on every start.
            await services.repo.replace_property_stock(load_property_stock(settings.property_stock_path))
            await services.repo.replace_demo_profiles(load_demo_profiles(settings.demo_profiles_path))
        log.info("startup", app_env=settings.app_env, rules_version=services.rules.version,
                 db=settings.db_backend, storage=settings.storage_backend, llm=settings.llm_backend,
                 kb=settings.kb_backend, auth=settings.auth_backend)
        yield

    app = FastAPI(title="LifeNest AI", version="0.1.0", lifespan=lifespan,
                  docs_url=None if settings.app_env == "prod" else "/docs", redoc_url=None)
    app.state.services = services
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_allowed_origins, allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(sessions.router)
    app.include_router(documents.router)
    app.include_router(actions.router)
    if isinstance(services.documents, LocalDocumentStore):
        app.include_router(documents.local_router)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, Any]:
        firestore_ok = await services.repo.db.ping()
        storage_ok = await services.documents.ping()
        kb_ok = services.kb is not None and await services.kb.store.ping()
        return {
            "status": "ok" if firestore_ok and storage_ok and kb_ok else "degraded",
            "firestore": "ok" if firestore_ok else "unavailable",
            "storage": "ok" if storage_ok else "unavailable",
            "kb": "ok" if kb_ok else "unavailable",
            "llm": settings.llm_backend,
            "rules_version": services.rules.version,
        }

    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    # Build lazily so importing app.main (e.g. in tests) never touches real backends.
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
