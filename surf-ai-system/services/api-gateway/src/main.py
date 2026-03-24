import os
import time
from typing import Any, Callable

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routes.admin import router as admin_router
from routes.auth import router as auth_router
from routes.users import router as users_router
from shared.utils.logger import get_logger
from shared.utils.metrics import MetricsRegistry
from shared.utils.pipeline_store import PipelineStore
from shared.utils.system_config import SystemConfigService
from src.db import SQLiteDB
from src.face_service import FaceUploadService
from src.media import MediaService
from src.rate_limit import SlidingWindowRateLimiter

logger = get_logger("api-gateway")


def _safe_init(
    app: FastAPI,
    *,
    component: str,
    factory: Callable[[], Any],
    detail_prefix: str,
) -> Any | None:
    try:
        instance = factory()
    except Exception as exc:
        detail = f"{detail_prefix}: {exc}"
        app.state.component_status[component] = {
            "ready": False,
            "detail": detail,
        }
        logger.error(detail, exc_info=True)
        return None

    app.state.component_status[component] = {
        "ready": True,
        "detail": None,
    }
    return instance


def _lazy_service_getter(
    app: FastAPI,
    *,
    component: str,
    state_key: str,
    factory: Callable[[], Any],
    detail_prefix: str,
) -> Callable[[], Any]:
    def _get_service() -> Any:
        existing = getattr(app.state, state_key, None)
        if existing is not None:
            return existing

        instance = _safe_init(
            app,
            component=component,
            factory=factory,
            detail_prefix=detail_prefix,
        )
        if instance is None:
            raise RuntimeError(app.state.component_status[component]["detail"])
        setattr(app.state, state_key, instance)
        return instance

    return _get_service


def _readiness_snapshot(app: FastAPI) -> tuple[bool, dict[str, dict[str, Any]]]:
    components = {
        key: dict(value)
        for key, value in getattr(app.state, "component_status", {}).items()
    }
    db = getattr(app.state, "db", None)
    db_ready = db is not None
    if db_ready:
        try:
            with db.store.connection() as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception as exc:
            db_ready = False
            components["database"] = {
                "ready": False,
                "detail": f"Database readiness check failed: {exc}",
            }
    else:
        components.setdefault(
            "database",
            {"ready": False, "detail": "Database dependency was not initialized"},
        )

    components.setdefault(
        "pipeline_store",
        {
            "ready": getattr(app.state, "pipeline_store", None) is not None,
            "detail": None
            if getattr(app.state, "pipeline_store", None) is not None
            else "Pipeline store dependency was not initialized",
        },
    )
    components.setdefault(
        "system_config",
        {
            "ready": getattr(app.state, "system_config", None) is not None,
            "detail": None
            if getattr(app.state, "system_config", None) is not None
            else "System config dependency was not initialized",
        },
    )

    ready = (
        db_ready
        and getattr(app.state, "pipeline_store", None) is not None
        and getattr(app.state, "system_config", None) is not None
    )
    return ready, components


def create_app() -> FastAPI:
    app = FastAPI(title="Surf AI API Gateway")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.started_at = time.time()
    app.state.release_version = (
        os.environ.get("APP_VERSION")
        or os.environ.get("DEPLOYMENT_VERSION")
        or os.environ.get("GIT_SHA")
        or "dev"
    )
    app.state.component_status = {}
    app.state.metrics = MetricsRegistry()
    app.state.db = _safe_init(
        app,
        component="database",
        factory=SQLiteDB,
        detail_prefix="Failed to initialize database",
    )
    if app.state.db is not None:
        app.state.pipeline_store = _safe_init(
            app,
            component="pipeline_store",
            factory=lambda: PipelineStore(app.state.db.db_path),
            detail_prefix="Failed to initialize pipeline store",
        )
        app.state.system_config = _safe_init(
            app,
            component="system_config",
            factory=lambda: SystemConfigService(app.state.db.db_path),
            detail_prefix="Failed to initialize system config",
        )
    else:
        app.state.pipeline_store = None
        app.state.system_config = None
        app.state.component_status["pipeline_store"] = {
            "ready": False,
            "detail": "Pipeline store skipped because database is unavailable",
        }
        app.state.component_status["system_config"] = {
            "ready": False,
            "detail": "System config skipped because database is unavailable",
        }
    app.state.media_service = _safe_init(
        app,
        component="media_service",
        factory=MediaService,
        detail_prefix="Failed to initialize media service",
    )
    app.state.face_service = None
    app.state.get_face_service = _lazy_service_getter(
        app,
        component="face_service",
        state_key="face_service",
        factory=FaceUploadService,
        detail_prefix="Failed to initialize face service",
    )
    app.state.admin_video_queue_url = os.environ.get("SQS_QUEUE_URL")
    app.state.matching_queue_url = os.environ.get("MATCHING_INPUT_SQS_URL")
    app.state.matching_min_track_consistency = float(
        os.environ.get("MIN_TRACK_CONSISTENCY", "0.75")
    )
    app.state.admin_sqs_client = _safe_init(
        app,
        component="sqs_client",
        factory=lambda: boto3.client(
            "sqs",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        ),
        detail_prefix="Failed to initialize SQS client",
    )
    app.state.upload_rate_limiter = SlidingWindowRateLimiter(
        max_requests=int(os.environ.get("UPLOAD_RATE_LIMIT_MAX_REQUESTS", "5")),
        window_seconds=int(os.environ.get("UPLOAD_RATE_LIMIT_WINDOW_SECONDS", "300")),
    )
    app.state.backfill_trigger_rate_limiter = SlidingWindowRateLimiter(
        max_requests=int(os.environ.get("BACKFILL_RATE_LIMIT_MAX_REQUESTS", "8")),
        window_seconds=int(os.environ.get("BACKFILL_RATE_LIMIT_WINDOW_SECONDS", "300")),
    )
    app.state.admin_backfill_rate_limiter = SlidingWindowRateLimiter(
        max_requests=int(os.environ.get("ADMIN_BACKFILL_RATE_LIMIT_MAX_REQUESTS", "3")),
        window_seconds=int(os.environ.get("ADMIN_BACKFILL_RATE_LIMIT_WINDOW_SECONDS", "300")),
    )

    app.include_router(auth_router, prefix="/auth", tags=["auth"])
    app.include_router(admin_router, prefix="/admin", tags=["admin"])
    app.include_router(users_router, tags=["users"])

    @app.get("/health")
    def health_check() -> JSONResponse:
        uptime_seconds = int(time.time() - app.state.started_at)
        ready, components = _readiness_snapshot(app)
        payload = {
            "status": "ok" if ready else "degraded",
            "ready": ready,
            "uptime_seconds": uptime_seconds,
            "version": app.state.release_version,
            "components": components,
        }
        return JSONResponse(
            status_code=200 if ready else 503,
            content=payload,
        )

    @app.get("/metrics")
    def metrics() -> dict[str, object]:
        ready, _ = _readiness_snapshot(app)
        return {
            "status": "ok" if ready else "degraded",
            "uptime_seconds": int(time.time() - app.state.started_at),
            "version": app.state.release_version,
            "counters": app.state.metrics.snapshot(),
        }

    if app.state.db is not None:
        logger.info(
            "API gateway started with SQLite storage at %s (version=%s)",
            app.state.db.db_path,
            app.state.release_version,
        )
    else:
        logger.warning(
            "API gateway started in degraded mode (version=%s)",
            app.state.release_version,
        )
    return app


app = create_app()
