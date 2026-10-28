"""FastAPI entry. The lifespan owns the database, Qdrant, watcher, and jobs."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from app import __version__
from app.auth import AuthMiddleware
from app.config import Settings, ensure_data_dirs
from app.db.database import Database
from app.db.repositories.jobs import RESTART_ERROR, JobRepository
from app.jobs import JobRunner, format_sse
from app.llm.ollama_client import OllamaClient, OllamaHealth
from app.mcp_server import create_mcp_app
from app.routes.bundles import install_bundle_routes
from app.routes.chat import install_chat_routes
from app.routes.documents import install_document_routes
from app.routes.google import install_google_routes
from app.routes.maintenance import install_maintenance_routes
from app.routes.matching import install_matching_routes
from app.routes.mcp_settings import install_mcp_settings_routes
from app.routes.projects import install_project_routes
from app.routes.stages import install_stage_routes
from app.runtime import Runtime
from app.secrets_store import SecretsStore, ensure_bearer, load_fernet
from app.services.bundle_ingest import register_bundle_handler
from app.services.chat import register_chat_handler
from app.services.doc_generator import register_document_handlers
from app.services.evaluator import register_evaluation_handler
from app.services.google_client import GoogleClient
from app.services.ingest import register_ingest_handler
from app.services.profile import register_profile_handler
from app.services.staging import StagingBuffer
from app.services.watcher import InboxWatcher
from app.vector_store import VectorStore

log = logging.getLogger(__name__)


class ComponentHealth(BaseModel):
    ok: bool
    detail: str | None = None
    loaded_models: list[str] | None = None


class HealthResponse(BaseModel):
    status: str
    db: ComponentHealth
    qdrant: ComponentHealth
    ollama: ComponentHealth
    watcher: ComponentHealth
    mcp: ComponentHealth


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Sparky_rATS", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    # Auth is added first so CORS stays outermost and can answer preflight.
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse, response_model_exclude_none=True)
    async def health(request: Request) -> HealthResponse:
        return await build_health(request.app)

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str, request: Request) -> JSONResponse:
        job = request.app.state.job_runner.get(job_id)
        if job is None:
            return JSONResponse({"detail": "job not found"}, status_code=404)
        return JSONResponse(job.public_dict())

    @app.post("/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, request: Request) -> JSONResponse:
        job = request.app.state.job_runner.request_cancel(job_id)
        if job is None:
            return JSONResponse({"detail": "job not found"}, status_code=404)
        return JSONResponse(job.public_dict())

    @app.get("/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> Response:
        runner: JobRunner = request.app.state.job_runner
        current = runner.get(job_id)
        if current is None:
            return JSONResponse({"detail": "job not found"}, status_code=404)

        async def stream() -> AsyncIterator[str]:
            queue = runner.subscribe(job_id)
            try:
                snapshot = runner.get(job_id)
                if snapshot is None:
                    return
                yield format_sse(snapshot)
                if snapshot.terminal:
                    return
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        snapshot = runner.get(job_id)
                        if snapshot is not None and snapshot.terminal:
                            yield format_sse(snapshot)
                            return
                        continue
                    yield format_sse(item)
                    if item.terminal:
                        return
            finally:
                runner.unsubscribe(job_id, queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    install_project_routes(app)
    install_bundle_routes(app)
    install_matching_routes(app)
    install_document_routes(app)
    install_stage_routes(app)
    install_google_routes(app)
    install_maintenance_routes(app)
    install_chat_routes(app)
    install_mcp_settings_routes(app)
    return app


async def build_health(app: FastAPI) -> HealthResponse:
    db_ok = True
    db_detail = None
    try:
        app.state.db.query("SELECT 1")
    except Exception as exc:
        db_ok = False
        db_detail = type(exc).__name__

    settings: Settings = app.state.settings
    vectors: VectorStore = app.state.vectors
    qdrant_ok = vectors.healthy()
    ollama: OllamaClient = app.state.ollama
    ollama_health: OllamaHealth = await ollama.health()
    watcher: InboxWatcher = app.state.watcher
    if settings.enable_watcher:
        watcher_health = ComponentHealth(ok=watcher.running())
    else:
        watcher_health = ComponentHealth(ok=False, detail="disabled")
    mcp_health = _mcp_health(app)

    required = [db_ok, qdrant_ok, ollama_health.ok]
    if settings.enable_watcher:
        required.append(watcher_health.ok)
    if settings.enable_mcp_server:
        required.append(mcp_health.ok)
    status = "ok" if all(required) else "degraded"
    return HealthResponse(
        status=status,
        db=ComponentHealth(ok=db_ok, detail=db_detail),
        qdrant=ComponentHealth(ok=qdrant_ok),
        ollama=ComponentHealth(
            ok=ollama_health.ok,
            detail=ollama_health.detail,
            loaded_models=ollama_health.loaded_models,
        ),
        watcher=watcher_health,
        mcp=mcp_health,
    )


def _mcp_health(app: FastAPI) -> ComponentHealth:
    settings: Settings = app.state.settings
    if not settings.enable_mcp_server:
        return ComponentHealth(ok=False, detail="disabled")
    server = getattr(app.state, "mcp_server", None)
    if server is not None and getattr(server, "started", False):
        return ComponentHealth(ok=True)
    detail = getattr(app.state, "mcp_error", None) or "starting"
    return ComponentHealth(ok=False, detail=str(detail))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    settings: Settings = app.state.settings
    ensure_data_dirs(settings)
    db = Database(settings.db_path)
    vectors: VectorStore | None = None
    watcher = InboxWatcher(settings.inbox_dir)
    ollama: OllamaClient | None = None
    google: GoogleClient | None = None
    runner: JobRunner | None = None
    runner_task: asyncio.Task[None] | None = None
    mcp_server: uvicorn.Server | None = None
    mcp_task: asyncio.Task[None] | None = None
    app.state.db = db
    app.state.watcher = watcher
    app.state.mcp_server = None
    app.state.mcp_error = None
    try:
        db.migrate()
        interrupted = JobRepository(db).mark_interrupted()
        if interrupted:
            log.info("marked %s running job(s) failed (%s)", interrupted, RESTART_ERROR)
        vectors = VectorStore.open(settings.qdrant_path)
        app.state.vectors = vectors
        secrets = SecretsStore(db, load_fernet(settings))
        app.state.secrets = secrets
        app.state.api_token = ensure_bearer(
            secrets,
            service="api_bearer",
            provided=settings.api_token,
            file_path=None if settings.api_token else settings.credentials_dir / "api_token",
        )
        app.state.mcp_token = ensure_bearer(
            secrets,
            service="mcp_bearer",
            provided=settings.mcp_token,
            file_path=None if settings.mcp_token else settings.credentials_dir / "mcp_token",
        )
        ollama = OllamaClient(settings)
        app.state.ollama = ollama
        runner = JobRunner(db, general_concurrency=settings.general_job_concurrency)
        register_ingest_handler(runner, db, ollama, settings)
        register_bundle_handler(runner, db, ollama, vectors, settings)
        register_evaluation_handler(runner, db, ollama, vectors, settings)
        register_profile_handler(runner, db, ollama)
        register_document_handlers(runner, db, ollama, vectors, settings)
        google = GoogleClient(settings, secrets)
        runtime = Runtime(
            settings=settings,
            db=db,
            ollama=ollama,
            vectors=vectors,
            jobs=runner,
            secrets=secrets,
            staging=StagingBuffer(),
            google=google,
        )
        app.state.runtime = runtime
        register_chat_handler(runner, runtime)
        app.state.job_runner = runner
        watcher = InboxWatcher(
            settings.inbox_dir,
            dest=settings.inbox_staging_dir,
            on_file=lambda path, original, digest: runner.enqueue_from_thread(
                "ingest_file",
                {
                    "path": str(path),
                    "filename": original,
                    "content_hash": digest,
                    "source": "inbox",
                },
            ),
            debounce_seconds=settings.inbox_debounce_seconds,
        )
        app.state.watcher = watcher
        if settings.enable_watcher:
            watcher.start()
        if settings.enable_mcp_server:
            mcp_server, mcp_task = _start_mcp(settings, app.state.mcp_token, runtime)
            app.state.mcp_server = mcp_server
        runner_task = asyncio.create_task(runner.loop())
        yield
    finally:
        if runner is not None:
            await runner.stop()
        if runner_task is not None:
            runner_task.cancel()
            with suppress(asyncio.CancelledError):
                await runner_task
        if mcp_server is not None:
            mcp_server.should_exit = True
        if mcp_task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await mcp_task
        watcher.stop()
        if google is not None:
            await google.aclose()
        if ollama is not None:
            await ollama.aclose()
        if vectors is not None:
            vectors.close()
        db.close()


def _start_mcp(
    settings: Settings,
    mcp_token: str,
    runtime: Runtime,
) -> tuple[uvicorn.Server, asyncio.Task[None]]:
    server = uvicorn.Server(
        uvicorn.Config(
            create_mcp_app(mcp_token, runtime),
            host=settings.api_host,
            port=settings.mcp_port,
            log_level="warning",
            access_log=False,
        )
    )
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    return server, asyncio.create_task(server.serve())


def _configure_logging() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )


app = create_app()
