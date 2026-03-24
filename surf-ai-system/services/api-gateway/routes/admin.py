import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field

from shared.utils.debug_compare import build_debug_compare_response
from shared.utils.embeddings import normalize_embeddings, pairwise_cosine_similarity
from shared.utils.logger import get_logger
from shared.utils.match_decision import evaluate_track_match
from shared.utils.system_config import (
    SYSTEM_CONFIG_DEFINITIONS,
    get_default_system_config,
)
from src.face_service import FaceUploadError
from src.security import get_current_user, is_admin_user

logger = get_logger("api-admin")

router = APIRouter()


class CameraRequest(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    active: bool = True
    camera_id: str | None = None


class PoolRequest(BaseModel):
    name: str = Field(min_length=1)


class VideoAssignmentRequest(BaseModel):
    user_id: str | None = None


class ConfigRollbackRequest(BaseModel):
    batch_id: str | None = None
    audit_id: int | None = None
    key: str | None = None


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if not is_admin_user(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required",
        )
    return current_user


def _serialize_pool(pool: dict | None) -> dict | None:
    if pool is None:
        return None
    return {
        "id": pool["pool_id"],
        "pool_id": pool["pool_id"],
        "name": pool["name"],
        "created_by": pool["created_by"],
        "created_at": pool["created_at"],
        "updated_at": pool["updated_at"],
    }


def _serialize_user_summary(request: Request, user: dict) -> dict:
    media = request.app.state.media_service
    reference_images = request.app.state.db.list_user_embeddings(user["user_id"])
    latest_reference = next(
        (item for item in reversed(reference_images) if item.get("source_image_s3")),
        None,
    )
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "role": user.get("role", "user"),
        "pool_id": user.get("pool_id"),
        "pool": _serialize_pool(user.get("pool")),
        "reference_images_count": len(reference_images),
        "latest_reference_image_url": None
        if latest_reference is None
        else media.get_presigned_url(latest_reference.get("source_image_s3")),
    }


def _runtime_config(request: Request) -> dict[str, int | float]:
    config_service = request.app.state.system_config
    defaults = get_default_system_config()
    return {
        key: config_service.get_config(key, default_value)
        for key, default_value in defaults.items()
    }


def _match_thresholds(request: Request) -> dict[str, float]:
    config = _runtime_config(request)
    return {
        "min_similarity": float(config["min_similarity"]),
        "min_margin": float(config["min_margin"]),
    }


def _matching_constraints(request: Request) -> dict[str, float | int]:
    config = _runtime_config(request)
    return {
        "min_track_embeddings": int(config["min_frames_per_track"]),
        "min_track_consistency": float(
            request.app.state.matching_min_track_consistency
        ),
    }


def _video_belongs_to_pool(video: dict[str, Any], pool_id: str | None) -> bool:
    if pool_id is None:
        return video.get("pool_id") is None
    return video.get("pool_id") == pool_id


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _duration_seconds(start_value: str | None, end_value: str | None) -> float | None:
    start_dt = _parse_iso_datetime(start_value)
    end_dt = _parse_iso_datetime(end_value)
    if start_dt is None or end_dt is None:
        return None
    return round(max((end_dt - start_dt).total_seconds(), 0.0), 3)


def _build_video_pipeline_summary(
    request: Request,
    *,
    video: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = video.get("diagnostics") or {}
    frame_data = diagnostics.get("frame_processor") or {}
    embedding_data = diagnostics.get("embedding_service") or {}
    matching_data = diagnostics.get("matching_service") or {}
    matching_tracks = matching_data.get("tracks") or {}
    embedding_tracks = embedding_data.get("tracks") or {}
    constraints = _matching_constraints(request)
    runtime_config = _runtime_config(request)

    total_tracks = max(
        int(frame_data.get("output_tracks", 0) or 0),
        int(embedding_data.get("tracks_received", 0) or 0),
        len(embedding_tracks),
        len(matching_tracks),
    )
    rejected_tracks = sum(
        1 for item in embedding_tracks.values() if item.get("status") == "rejected"
    )
    matched_tracks = sum(
        1 for item in matching_tracks.values() if item.get("decision") == "match"
    )
    unmatched_tracks = sum(
        1 for item in matching_tracks.values() if item.get("decision") == "no_match"
    ) + rejected_tracks
    processed_tracks = matched_tracks + unmatched_tracks
    pending_tracks = max(total_tracks - processed_tracks, 0)

    similarities = [
        float(item["best_similarity"])
        for item in matching_tracks.values()
        if item.get("best_similarity") is not None
    ]
    margins = [
        float(item["margin"])
        for item in matching_tracks.values()
        if item.get("margin") is not None
    ]
    matches = request.app.state.db.list_matches_for_video(
        video_id=video["video_id"],
        pool_id=video.get("pool_id"),
    )
    matches_count = len(matches)
    rejection_rate = None
    if processed_tracks > 0:
        rejection_rate = round((unmatched_tracks / processed_tracks) * 100.0, 2)

    stage_status = {
        "upload": "completed",
        "frame": "completed"
        if frame_data.get("completed_at")
        else ("processing" if frame_data.get("started_at") else "pending"),
        "embedding": "completed"
        if total_tracks > 0 and int(embedding_data.get("tracks_received", 0) or 0) >= total_tracks
        else ("processing" if embedding_data.get("started_at") else "pending"),
        "matching": "completed"
        if total_tracks == 0 or pending_tracks == 0
        else ("processing" if matching_tracks else "pending"),
    }
    progress_percent = 10
    if frame_data.get("started_at"):
        progress_percent = 30
    if frame_data.get("completed_at"):
        progress_percent = 50
    if embedding_data.get("started_at"):
        progress_percent = 65
    if total_tracks > 0:
        progress_percent = max(
            progress_percent,
            min(99, int(round(65 + (35 * (processed_tracks / total_tracks))))),
        )
    if video.get("status") == "completed":
        progress_percent = 100
    if video.get("status") == "failed":
        progress_percent = max(progress_percent, 1)

    final_completed_at = (
        matching_data.get("completed_at")
        or embedding_data.get("completed_at")
        or frame_data.get("completed_at")
        or video.get("updated_at")
    )
    stage_timings = {
        "upload_seconds": (diagnostics.get("ingestion_service") or {}).get("upload_seconds"),
        "queue_delay_seconds": frame_data.get("queue_delay_seconds"),
        "frame_processing_seconds": frame_data.get("processing_seconds"),
        "embedding_processing_seconds": embedding_data.get("processing_seconds"),
        "matching_processing_seconds": matching_data.get("processing_seconds"),
        "total_pipeline_seconds": _duration_seconds(video.get("created_at"), final_completed_at),
    }
    quality_guard = {
        "min_frames_per_track": int(constraints["min_track_embeddings"]),
        "min_track_consistency": float(constraints["min_track_consistency"]),
        "min_quality_score": float(runtime_config["min_quality_score"]),
        "rejection_counts": embedding_data.get("rejection_counts") or {},
    }

    return {
        "progress_percent": progress_percent,
        "stage_status": stage_status,
        "stage_timings": stage_timings,
        "tracks_total": total_tracks,
        "tracks_processed": processed_tracks,
        "tracks_pending": pending_tracks,
        "tracks_matched": matched_tracks,
        "tracks_unmatched": unmatched_tracks,
        "tracks_rejected": rejected_tracks,
        "matches_count": matches_count,
        "rejection_rate": rejection_rate,
        "avg_similarity": None
        if not similarities
        else round(sum(similarities) / len(similarities), 4),
        "avg_margin": None if not margins else round(sum(margins) / len(margins), 4),
        "quality_guard": quality_guard,
    }


def _build_compare_response(
    request: Request,
    *,
    video_id: str,
    pool_id: str | None,
) -> dict[str, Any]:
    media = request.app.state.media_service
    video = request.app.state.pipeline_store.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if not _video_belongs_to_pool(video, pool_id):
        raise HTTPException(status_code=404, detail="Video not found in the active pool")

    pool_users = request.app.state.db.list_users(pool_id=pool_id) if pool_id else []
    user_lookup = {user["user_id"]: user for user in pool_users}
    pool_reference_images = [
        {
            **item,
            "source_image_url": media.get_presigned_url(item.get("source_image_s3")),
        }
        for item in (request.app.state.db.list_pool_reference_images(pool_id) if pool_id else [])
    ]
    video_embeddings = [
        {
            **item,
            "keyframe_url": media.get_presigned_url(item.get("keyframe_s3"))
            or media.get_presigned_url(key=f"thumbnails/{item.get('track_id')}.jpg"),
        }
        for item in request.app.state.pipeline_store.list_video_embeddings(video_id)
    ]
    frame_embeddings_lookup = {
        (item["track_id"], int(item["frame_index"])): item
        for item in request.app.state.pipeline_store.list_video_frame_embeddings(video_id)
    }
    debug_frames = [
        {
            **item,
            "image_url": media.get_presigned_url(item.get("image_s3")),
        }
        for item in request.app.state.pipeline_store.list_video_debug_frames(video_id)
    ]
    thresholds = _match_thresholds(request)
    constraints = _matching_constraints(request)
    matching_attempts = (
        ((video.get("diagnostics") or {}).get("matching_service") or {}).get("tracks")
        or {}
    )
    matches = request.app.state.db.list_matches_for_video(video_id=video_id, pool_id=pool_id)
    return build_debug_compare_response(
        video_id=video_id,
        video=video,
        pool=_serialize_pool(request.app.state.db.get_pool(video.get("pool_id"))),
        pool_users=pool_users,
        pool_reference_images=pool_reference_images,
        video_embeddings=video_embeddings,
        frame_embeddings=list(frame_embeddings_lookup.values()),
        debug_frames=debug_frames,
        matches=matches,
        similarity_threshold=thresholds["min_similarity"],
        margin_threshold=thresholds["min_margin"],
        min_track_embeddings=int(constraints["min_track_embeddings"]),
        min_track_consistency=float(constraints["min_track_consistency"]),
        matching_attempts=matching_attempts,
    )


def _build_video_debug_summary(
    request: Request,
    *,
    video_id: str,
    pool_id: str | None,
) -> dict[str, Any]:
    compare_response = _build_compare_response(request, video_id=video_id, pool_id=pool_id)
    best_match = compare_response["comparisons"][0] if compare_response["comparisons"] else None
    best_confirmed_match = compare_response["matches"][0] if compare_response["matches"] else None
    return {
        "pool_id": compare_response["pool_id"],
        "pool_users_count": compare_response["pool_users"],
        "user_embeddings_count": compare_response["user_embeddings"],
        "video_embeddings_count": compare_response["video_embeddings"],
        "min_distance": None if best_match is None else best_match["distance"],
        "best_similarity": None if best_match is None else best_match["similarity"],
        "best_match_user_id": None if best_match is None else best_match["user_id"],
        "best_match_user_email": None if best_match is None else best_match["user_email"],
        "confirmed_match_user_id": None if best_confirmed_match is None else best_confirmed_match["user_id"],
        "confirmed_match_user_email": None if best_confirmed_match is None else best_confirmed_match["email"],
        "assigned_user_id": compare_response["assigned_user_id"],
        "assigned_user_email": compare_response["assigned_user_email"],
        "threshold": compare_response["threshold"],
        "margin_threshold": compare_response.get("margin_threshold"),
    }


@router.get("/pools")
def list_pools(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> list[dict]:
    _ = current_user
    return [_serialize_pool(pool) for pool in request.app.state.db.list_pools()]


@router.post("/pools")
def create_pool(
    payload: PoolRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict:
    try:
        pool = request.app.state.db.create_pool(
            name=payload.name,
            created_by=current_user["user_id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("Pool created: pool_id=%s user_id=%s", pool["pool_id"], current_user["user_id"])
    return _serialize_pool(pool)


@router.get("/users")
def list_pool_users(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> list[dict]:
    if not current_user.get("pool_id"):
        return []
    return [
        _serialize_user_summary(request, user)
        for user in request.app.state.db.list_users(pool_id=current_user["pool_id"])
    ]


@router.post("/upload-video")
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(require_admin),
) -> dict:
    if not current_user.get("pool_id"):
        raise HTTPException(status_code=400, detail="Select an active pool before uploading videos")

    video_bytes = await file.read()
    if not video_bytes:
        raise HTTPException(status_code=400, detail="Video file is required")

    queue_url = request.app.state.admin_video_queue_url
    if not queue_url:
        raise HTTPException(status_code=500, detail="SQS queue is not configured")

    video_id = str(uuid.uuid4())
    _, extension = os.path.splitext(file.filename or "")
    object_key = f"uploads/videos/{video_id}{extension or '.mp4'}"
    upload_started_at = datetime.now(timezone.utc).isoformat()
    planned_s3_path = (
        f"s3://{request.app.state.media_service.default_bucket}/{object_key}"
    )
    video_record = request.app.state.pipeline_store.create_video(
        video_id=video_id,
        s3_path=planned_s3_path,
        status="uploaded",
        pool_id=current_user["pool_id"],
    )

    try:
        s3_upload_started_at = datetime.now(timezone.utc).isoformat()
        request.app.state.media_service.upload_bytes(
            data=video_bytes,
            key=object_key,
            content_type=file.content_type,
        )
        upload_completed_at = datetime.now(timezone.utc).isoformat()
        request.app.state.pipeline_store.update_video_diagnostics(
            video_id,
            {
                "ingestion_service": {
                    "upload_started_at": upload_started_at,
                    "s3_upload_started_at": s3_upload_started_at,
                    "upload_completed_at": upload_completed_at,
                    "upload_seconds": _duration_seconds(upload_started_at, upload_completed_at),
                    "file_name": file.filename,
                    "file_size_bytes": len(video_bytes),
                }
            },
        )

        queue_response = request.app.state.admin_sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {
                    "video_id": video_id,
                    "pool_id": current_user["pool_id"],
                    "s3_path": planned_s3_path,
                    "type": "video",
                    "timestamp": video_record["created_at"],
                }
            ),
        )
        request.app.state.pipeline_store.update_video_diagnostics(
            video_id,
            {
                "ingestion_service": {
                    "queued_at": upload_completed_at,
                    "queue_message_id": queue_response.get("MessageId"),
                }
            },
        )
    except Exception as exc:
        logger.error("Video upload failed for admin user %s: %s", current_user["user_id"], exc, exc_info=True)
        request.app.state.pipeline_store.update_video_diagnostics(
            video_id,
            {
                "ingestion_service": {
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                    "error": str(exc),
                }
            },
        )
        request.app.state.pipeline_store.update_video_status(
            video_id,
            "failed",
            error_message=str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Unable to upload and queue video",
                "video_id": video_id,
            },
        ) from exc

    logger.info(
        "Video queued: video_id=%s user_id=%s pool_id=%s",
        video_id,
        current_user["user_id"],
        current_user["pool_id"],
    )
    return {
        **video_record,
        "message": "Video uploaded and queued for processing",
    }


@router.post("/camera")
def upsert_camera(
    payload: CameraRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict:
    if not current_user.get("pool_id"):
        raise HTTPException(status_code=400, detail="Select an active pool before saving cameras")

    camera = request.app.state.pipeline_store.upsert_camera(
        camera_id=payload.camera_id,
        name=payload.name,
        url=payload.url,
        active=payload.active,
        pool_id=current_user["pool_id"],
    )
    logger.info("Camera saved: camera_id=%s user_id=%s", camera["camera_id"], current_user["user_id"])
    return camera


@router.get("/cameras")
def list_cameras(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> list[dict]:
    if not current_user.get("pool_id"):
        return []
    return request.app.state.pipeline_store.list_cameras(pool_id=current_user["pool_id"])


@router.get("/videos")
def list_videos(
    request: Request,
    include_debug: bool = Query(True),
    current_user: dict = Depends(require_admin),
) -> list[dict]:
    if not current_user.get("pool_id"):
        return []

    media = request.app.state.media_service
    videos = request.app.state.pipeline_store.list_videos(pool_id=current_user["pool_id"])
    return [
        {
            **video,
            "source_video_url": media.get_presigned_url(video.get("s3_path")),
            **_build_video_pipeline_summary(request, video=video),
            **(
                _build_video_debug_summary(
                    request,
                    video_id=video["video_id"],
                    pool_id=current_user["pool_id"],
                )
                if include_debug
                else {}
            ),
        }
        for video in videos
    ]


@router.get("/debug/compare/{video_id}")
def debug_compare_video(
    video_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    return _build_compare_response(
        request,
        video_id=video_id,
        pool_id=current_user.get("pool_id"),
    )


@router.get("/config")
def get_system_config(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict[str, int | float]:
    _ = current_user
    return _runtime_config(request)


@router.get("/config/status")
def get_system_config_status(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    _ = current_user
    return request.app.state.system_config.get_update_guard_state()


@router.put("/config")
def update_system_config(
    payload: dict[str, float],
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict[str, int | float]:
    if not payload:
        raise HTTPException(status_code=400, detail="At least one config value is required")

    unsupported_keys = sorted(key for key in payload if key not in SYSTEM_CONFIG_DEFINITIONS)
    if unsupported_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported config keys: {', '.join(unsupported_keys)}",
        )

    try:
        return request.app.state.system_config.update_config(
            payload,
            updated_by=current_user.get("email") or current_user["user_id"],
            admin_id=current_user["user_id"],
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/config/history")
def get_system_config_history(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(require_admin),
) -> list[dict[str, Any]]:
    _ = current_user
    return request.app.state.system_config.list_change_history(limit=limit)


@router.post("/config/rollback")
def rollback_system_config(
    payload: ConfigRollbackRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    try:
        return request.app.state.system_config.rollback_config(
            updated_by=current_user.get("email") or current_user["user_id"],
            admin_id=current_user["user_id"],
            batch_id=payload.batch_id,
            audit_id=payload.audit_id,
            key=payload.key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/metrics")
def get_admin_metrics(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    _ = current_user
    matching_metrics = request.app.state.pipeline_store.get_metrics(prefix="matching.")
    embedding_metrics = request.app.state.pipeline_store.get_metrics(prefix="embedding.")
    stats = request.app.state.db.get_match_statistics(pool_id=current_user.get("pool_id"))
    videos = request.app.state.pipeline_store.list_videos(pool_id=current_user.get("pool_id"))
    video_summaries = [
        {
            "video_id": video["video_id"],
            "created_at": video["created_at"],
            "status": video["status"],
            **_build_video_pipeline_summary(request, video=video),
        }
        for video in videos
    ]
    total_processed = sum(int(item["tracks_processed"]) for item in video_summaries)
    total_unmatched = sum(int(item["tracks_unmatched"]) for item in video_summaries)
    return {
        "matching": {
            **matching_metrics,
            **embedding_metrics,
            "average_match_similarity": stats["average_similarity"],
            "average_match_margin": stats["average_margin"],
            "rejection_rate": 0.0
            if total_processed == 0
            else round((total_unmatched / total_processed) * 100.0, 2),
        },
        "videos": {
            "matches_per_video": [
                {
                    "video_id": item["video_id"],
                    "status": item["status"],
                    "matches_count": item["matches_count"],
                    "tracks_matched": item["tracks_matched"],
                    "tracks_unmatched": item["tracks_unmatched"],
                    "progress_percent": item["progress_percent"],
                    "avg_similarity": item["avg_similarity"],
                    "avg_margin": item["avg_margin"],
                }
                for item in video_summaries
            ],
        },
        "jobs": request.app.state.pipeline_store.list_recent_jobs(limit=25),
        "config_status": request.app.state.system_config.get_update_guard_state(),
        "gateway": request.app.state.metrics.snapshot(),
    }


@router.post("/compare-faces")
async def compare_faces(
    request: Request,
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    current_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    _ = current_user

    image_a = await file1.read()
    image_b = await file2.read()
    if not image_a or not image_b:
        raise HTTPException(status_code=400, detail="Both face images are required")

    try:
        face_service = request.app.state.get_face_service()
    except RuntimeError as exc:
        logger.error("Face comparison requested while face service is unavailable: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="Face service is unavailable") from exc

    try:
        result_a = face_service.extract_embedding(
            image_a,
            allow_multiple_faces=True,
        )
        result_b = face_service.extract_embedding(
            image_b,
            allow_multiple_faces=True,
        )
    except FaceUploadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    similarity = float(
        pairwise_cosine_similarity(
            [result_a["embedding"]],
            [result_b["embedding"]],
        )[0, 0]
    )
    thresholds = _match_thresholds(request)
    constraints = _matching_constraints(request)
    candidate_embeddings = normalize_embeddings([result_b["embedding"]])
    evaluation = evaluate_track_match(
        track_embedding=result_a["embedding"],
        users=[
            {
                "user_id": "calibration-target",
                "email": "Calibration target",
                "embedding_ids": ["calibration-target"],
                "embeddings": candidate_embeddings,
                "avg_embedding": candidate_embeddings[0],
            }
        ],
        similarity_threshold=thresholds["min_similarity"],
        margin_threshold=thresholds["min_margin"],
        min_track_embeddings=int(constraints["min_track_embeddings"]),
        min_track_consistency=float(constraints["min_track_consistency"]),
        evidence_count=max(1, int(constraints["min_track_embeddings"])),
        payload_consistency=1.0,
        quality_avg=None,
    )
    decision = evaluation.decision
    margin_warning = None
    if similarity < 0.6:
        margin_warning = "Similarity is below 0.60. These thresholds are likely unrealistic for a reliable match."
    return {
        "similarity": similarity,
        "distance": None if decision.best_similarity is None else float(1.0 - decision.best_similarity),
        "best_similarity": decision.best_similarity,
        "second_best_similarity": decision.second_best_similarity,
        "margin": decision.margin,
        "passes_similarity": decision.passes_similarity,
        "passes_margin": decision.passes_margin,
        "passes_margin_estimate": decision.passes_margin,
        "estimated_margin": decision.margin,
        "final_verdict": decision.final_verdict,
        "rejection_reason": decision.decision_reason,
        "decision_reason": decision.decision_reason,
        "explanation": decision.explanation,
        "decision_explanation": decision.explanation,
        "verdict": decision.final_verdict,
        "threshold": decision.threshold_used,
        "threshold_used": decision.threshold_used,
        "margin_threshold": decision.margin_threshold_used,
        "margin_threshold_used": decision.margin_threshold_used,
        "thresholds": thresholds,
        "warning": margin_warning,
        "comparison_mode": "single_reference_matcher_path",
    }


@router.post("/videos/{video_id}/assign")
def assign_video_user(
    video_id: str,
    payload: VideoAssignmentRequest,
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict:
    video = request.app.state.pipeline_store.get_video(video_id)
    if not video or not _video_belongs_to_pool(video, current_user.get("pool_id")):
        raise HTTPException(status_code=404, detail="Video not found")

    assigned_user_id = payload.user_id
    if assigned_user_id is not None:
        target_user = request.app.state.db.get_user_by_id(assigned_user_id)
        if not target_user or target_user.get("pool_id") != current_user.get("pool_id"):
            raise HTTPException(status_code=404, detail="User not found in the active pool")

    updated_video = request.app.state.pipeline_store.assign_video_user(
        video_id=video_id,
        user_id=assigned_user_id,
    )
    if not updated_video:
        raise HTTPException(status_code=404, detail="Video not found")

    assigned_user = (
        request.app.state.db.get_user_by_id(assigned_user_id)
        if assigned_user_id
        else None
    )
    return {
        "video_id": video_id,
        "assigned_user_id": assigned_user_id,
        "assigned_user_email": None if assigned_user is None else assigned_user["email"],
        "message": "Video assignment updated",
    }


@router.post("/videos/{video_id}/process")
def trigger_video_processing(
    video_id: str,
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict:
    queue_url = request.app.state.admin_video_queue_url
    if not queue_url:
        raise HTTPException(status_code=500, detail="SQS queue is not configured")

    video = request.app.state.pipeline_store.get_video(video_id)
    if not video or not _video_belongs_to_pool(video, current_user.get("pool_id")):
        raise HTTPException(status_code=404, detail="Video not found")

    queued_at = datetime.now(timezone.utc).isoformat()
    try:
        request.app.state.pipeline_store.update_video_status(video_id, "uploaded")
        queue_response = request.app.state.admin_sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {
                    "video_id": video["video_id"],
                    "pool_id": video.get("pool_id"),
                    "s3_path": video["s3_path"],
                    "type": "video",
                    "timestamp": queued_at,
                }
            ),
        )
        request.app.state.pipeline_store.update_video_diagnostics(
            video_id,
            {
                "ingestion_service": {
                    "requeued_at": queued_at,
                    "queued_at": queued_at,
                    "queue_message_id": queue_response.get("MessageId"),
                }
            },
        )
    except Exception as exc:
        logger.error("Failed to requeue video %s by user %s: %s", video_id, current_user["user_id"], exc, exc_info=True)
        request.app.state.pipeline_store.update_video_status(
            video_id,
            "failed",
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail="Unable to queue video") from exc

    logger.info("Video requeued: video_id=%s user_id=%s", video_id, current_user["user_id"])
    return {
        "video_id": video["video_id"],
        "status": "uploaded",
        "queued_at": queued_at,
        "message": "Video queued for processing",
    }


@router.post("/rematch-pool")
def rematch_pool(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> dict:
    pool_id = current_user.get("pool_id")
    if not pool_id:
        raise HTTPException(status_code=400, detail="Select an active pool before rematching")

    queue_url = request.app.state.matching_queue_url
    if not queue_url:
        raise HTTPException(status_code=500, detail="Matching queue is not configured")

    queued_at = datetime.now(timezone.utc).isoformat()
    try:
        request.app.state.admin_backfill_rate_limiter.check(
            f"admin-rematch:{current_user['user_id']}:{pool_id}",
            code="backfill_rate_limited",
            message="Too many backfill triggers. Please wait before queueing another pool rematch.",
        )
    except HTTPException as exc:
        raise HTTPException(status_code=429, detail=exc.detail) from exc

    try:
        request.app.state.admin_sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {
                    "job_type": "rematch_pool_tracks",
                    "job_id": str(uuid.uuid4()),
                    "pool_id": pool_id,
                    "batch_size": 100,
                    "queued_at": queued_at,
                }
            ),
        )
    except Exception as exc:
        logger.error(
            "Failed to queue rematch for pool_id=%s user_id=%s: %s",
            pool_id,
            current_user["user_id"],
            exc,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Unable to queue pool rematch") from exc

    return {
        "pool_id": pool_id,
        "queued_at": queued_at,
        "message": "Pool rematch queued",
    }
