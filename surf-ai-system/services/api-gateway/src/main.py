import os
import time

import boto3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes.admin import router as admin_router
from routes.auth import router as auth_router
from routes.users import router as users_router
from shared.utils.logger import get_logger
from shared.utils.metrics import MetricsRegistry
from shared.utils.pipeline_store import PipelineStore
from src.db import SQLiteDB
from src.face_service import FaceUploadService
from src.media import MediaService
from src.rate_limit import SlidingWindowRateLimiter
from src.security import get_jwt_config

logger = get_logger("api-gateway")


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
    app.state.jwt_config = get_jwt_config()
    app.state.db = SQLiteDB()
    app.state.pipeline_store = PipelineStore(app.state.db.db_path)
    app.state.face_service = FaceUploadService()
    app.state.media_service = MediaService()
    app.state.metrics = MetricsRegistry()
    app.state.admin_video_queue_url = os.environ.get("SQS_QUEUE_URL")
    app.state.admin_sqs_client = boto3.client(
        "sqs",
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )
    app.state.upload_rate_limiter = SlidingWindowRateLimiter(
        max_requests=int(os.environ.get("UPLOAD_RATE_LIMIT_MAX_REQUESTS", "5")),
        window_seconds=int(os.environ.get("UPLOAD_RATE_LIMIT_WINDOW_SECONDS", "300")),
    )

    app.include_router(auth_router, prefix="/auth", tags=["auth"])
    app.include_router(admin_router, prefix="/admin", tags=["admin"])
    app.include_router(users_router, tags=["users"])

    @app.get("/health")
    def health_check() -> dict[str, str | int]:
        uptime_seconds = int(time.time() - app.state.started_at)
        return {"status": "ok", "uptime_seconds": uptime_seconds}

    @app.get("/metrics")
    def metrics() -> dict[str, object]:
        return {
            "status": "ok",
            "uptime_seconds": int(time.time() - app.state.started_at),
            "counters": app.state.metrics.snapshot(),
        }

    logger.info("API gateway started with SQLite storage at %s", app.state.db.db_path)
    return app


app = create_app()
