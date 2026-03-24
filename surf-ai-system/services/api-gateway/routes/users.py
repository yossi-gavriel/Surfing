from collections import defaultdict
import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from shared.utils.logger import get_logger
from src.face_service import FaceUploadError
from src.security import get_current_user

logger = get_logger("api-users")

router = APIRouter()


class PoolSelectionRequest(BaseModel):
    pool_id: str


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


def _serialize_reference_image(request: Request, item: dict) -> dict:
    media = request.app.state.media_service
    return {
        "id": item["reference_image_id"],
        "reference_image_id": item["reference_image_id"],
        "user_embedding_id": item["user_embedding_id"],
        "user_id": item["user_id"],
        "image_url": media.get_presigned_url(item.get("source_image_s3")),
        "source_image_s3": item.get("source_image_s3"),
        "created_at": item.get("created_at"),
    }


def _serialize_me(request: Request, user: dict) -> dict:
    reference_images = request.app.state.db.list_user_embeddings(user["user_id"])
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "role": user.get("role", "user"),
        "pool_id": user.get("pool_id"),
        "pool": _serialize_pool(user.get("pool")),
        "reference_images_count": len(reference_images),
    }


def _queue_user_backfill(
    request: Request,
    *,
    current_user: dict,
    reference_image_record: dict,
) -> dict:
    queue_url = request.app.state.matching_queue_url
    if not queue_url or not current_user.get("pool_id"):
        return {
            "queued": False,
            "job_id": None,
            "user_embedding_id": reference_image_record["user_embedding_id"],
            "queued_at": None,
        }

    try:
        request.app.state.backfill_trigger_rate_limiter.check(
            f"backfill:{current_user['user_id']}",
            code="backfill_rate_limited",
            message="Too many backfill triggers. Please wait before queueing more matching work.",
        )
        job_id = str(uuid.uuid4())
        queued_at = datetime.now(timezone.utc).isoformat()
        request.app.state.admin_sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {
                    "job_type": "backfill_user_matches",
                    "job_id": job_id,
                    "idempotency_key": (
                        f"backfill:{current_user['pool_id']}:{current_user['user_id']}:"
                        f"{reference_image_record['user_embedding_id']}"
                    ),
                    "pool_id": current_user["pool_id"],
                    "user_id": current_user["user_id"],
                    "user_embedding_id": reference_image_record["user_embedding_id"],
                    "user_embedding": reference_image_record["embedding"],
                    "batch_size": 100,
                    "queued_at": queued_at,
                }
            ),
        )
        return {
            "queued": True,
            "job_id": job_id,
            "user_embedding_id": reference_image_record["user_embedding_id"],
            "queued_at": queued_at,
        }
    except HTTPException as exc:
        logger.warning(
            "Backfill rate limited for user_id=%s reference_image_id=%s: %s",
            current_user["user_id"],
            reference_image_record["user_embedding_id"],
            exc.detail,
        )
        request.app.state.metrics.increment("backfill.rate_limited")
        return {
            "queued": False,
            "job_id": None,
            "user_embedding_id": reference_image_record["user_embedding_id"],
            "queued_at": None,
        }
    except Exception as exc:
        logger.error(
            "Failed to queue backfill for user_id=%s reference_image_id=%s: %s",
            current_user["user_id"],
            reference_image_record["user_embedding_id"],
            exc,
            exc_info=True,
        )
        return {
            "queued": False,
            "job_id": None,
            "user_embedding_id": reference_image_record["user_embedding_id"],
            "queued_at": None,
        }


def _matching_job_ids(raw_value: str | None) -> set[str]:
    if not raw_value:
        return set()
    return {part.strip() for part in raw_value.split(",") if part.strip()}


def _summarize_backfill_status(
    request: Request,
    *,
    current_user: dict,
    job_ids: set[str] | None = None,
) -> dict:
    relevant_jobs = []
    for job in request.app.state.pipeline_store.list_recent_jobs(
        limit=200,
        job_type="backfill_user_matches",
    ):
        payload = job.get("payload") or {}
        if payload.get("user_id") != current_user["user_id"]:
            continue
        if payload.get("pool_id") != current_user.get("pool_id"):
            continue
        if job_ids and job.get("job_id") not in job_ids:
            continue
        relevant_jobs.append(job)

    grouped: dict[str, dict] = {}
    for job in relevant_jobs:
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue
        payload = job.get("payload") or {}
        summary = grouped.setdefault(
            job_id,
            {
                "job_id": job_id,
                "status": "running",
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "updated_at": job.get("updated_at"),
                "last_error": job.get("last_error"),
                "user_embedding_id": payload.get("user_embedding_id"),
                "batches_total": 0,
                "batches_completed": 0,
                "batches_failed": 0,
                "batches_running": 0,
            },
        )
        summary["batches_total"] += 1
        summary["started_at"] = min(
            [value for value in [summary.get("started_at"), job.get("started_at")] if value],
            default=summary.get("started_at") or job.get("started_at"),
        )
        summary["updated_at"] = max(
            [value for value in [summary.get("updated_at"), job.get("updated_at")] if value],
            default=summary.get("updated_at") or job.get("updated_at"),
        )
        if job.get("status") == "completed":
            summary["batches_completed"] += 1
            summary["completed_at"] = max(
                [value for value in [summary.get("completed_at"), job.get("completed_at")] if value],
                default=summary.get("completed_at") or job.get("completed_at"),
            )
        elif job.get("status") == "failed":
            summary["batches_failed"] += 1
            summary["last_error"] = job.get("last_error") or summary.get("last_error")
        else:
            summary["batches_running"] += 1

    job_summaries = list(grouped.values())
    for summary in job_summaries:
        if summary["batches_running"] > 0:
            summary["status"] = "running"
        elif summary["batches_failed"] > 0:
            summary["status"] = "failed"
        else:
            summary["status"] = "done"

    requested_ids = set(job_ids or set())
    seen_ids = {summary["job_id"] for summary in job_summaries}
    missing_requested = requested_ids - seen_ids
    jobs_total = len(requested_ids) if requested_ids else len(job_summaries)
    jobs_completed = sum(1 for summary in job_summaries if summary["status"] == "done")
    jobs_failed = sum(1 for summary in job_summaries if summary["status"] == "failed")
    jobs_running = sum(1 for summary in job_summaries if summary["status"] == "running")
    jobs_pending = max(jobs_total - jobs_completed - jobs_failed - jobs_running, 0)

    if jobs_total == 0:
        status_value = "idle"
        message = "No backfill running"
    elif jobs_running > 0 or missing_requested:
        status_value = "running"
        message = "Backfill running..."
    elif jobs_failed > 0:
        status_value = "failed"
        message = "Backfill failed"
    else:
        status_value = "done"
        message = "Backfill done"

    return {
        "status": status_value,
        "message": message,
        "job_ids": sorted(requested_ids or seen_ids),
        "jobs_total": jobs_total,
        "jobs_completed": jobs_completed,
        "jobs_failed": jobs_failed,
        "jobs_running": jobs_running,
        "jobs_pending": jobs_pending,
        "jobs": sorted(job_summaries, key=lambda item: item.get("updated_at") or "", reverse=True),
    }


async def _store_reference_image(
    request: Request,
    *,
    current_user: dict,
    file: UploadFile,
) -> dict:
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is required")

    try:
        face_service = request.app.state.get_face_service()
    except RuntimeError as exc:
        logger.error("Face service unavailable while uploading reference image: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="Face service is unavailable") from exc

    try:
        result = face_service.extract_embedding(image_bytes)
    except FaceUploadError as exc:
        request.app.state.metrics.increment(f"upload_face.error.{exc.code}")
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    source_image_s3 = None
    try:
        _, extension = os.path.splitext(file.filename or "")
        source_image_s3 = request.app.state.media_service.upload_bytes(
            data=image_bytes,
            key=f"uploads/reference-faces/{current_user['user_id']}/{uuid.uuid4()}{extension or '.jpg'}",
            content_type=file.content_type or "image/jpeg",
        )
    except Exception as exc:
        logger.warning(
            "Reference face image upload failed for user_id=%s: %s",
            current_user["user_id"],
            exc,
        )

    updated_user = request.app.state.db.append_user_embedding(
        user_id=current_user["user_id"],
        embedding=result["embedding"],
        source_image_s3=source_image_s3,
    )
    if not updated_user:
        raise HTTPException(status_code=404, detail="User not found")

    latest_reference = request.app.state.db.list_user_embeddings(current_user["user_id"])[-1]
    return {
        "updated_user": updated_user,
        "reference_image": _serialize_reference_image(request, latest_reference),
        "reference_image_record": latest_reference,
    }


@router.get("/me")
def get_me(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    return _serialize_me(request, current_user)


@router.get("/pools")
def list_pools(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    _ = current_user
    return [_serialize_pool(pool) for pool in request.app.state.db.list_pools()]


@router.put("/me/pool")
def update_my_pool(
    payload: PoolSelectionRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    try:
        user = request.app.state.db.update_user_pool(
            user_id=current_user["user_id"],
            pool_id=payload.pool_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _serialize_me(request, user)


@router.get("/me/reference-images")
def get_reference_images(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> list[dict]:
    return [
        _serialize_reference_image(request, item)
        for item in request.app.state.db.list_user_embeddings(current_user["user_id"])
    ]


@router.get("/me/backfill-status")
def get_backfill_status(
    request: Request,
    job_ids: str | None = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> dict:
    return _summarize_backfill_status(
        request,
        current_user=current_user,
        job_ids=_matching_job_ids(job_ids),
    )


@router.post("/me/reference-images")
async def upload_reference_images(
    request: Request,
    current_user: dict = Depends(get_current_user),
    files: list[UploadFile] = File(...),
) -> dict:
    if not current_user.get("pool_id"):
        raise HTTPException(status_code=400, detail="Select a pool before uploading reference images")

    client_ip = request.client.host if request.client else "unknown"
    request.app.state.metrics.increment("upload_face.attempt")
    try:
        request.app.state.upload_rate_limiter.check(f"{current_user['user_id']}:{client_ip}")
    except HTTPException:
        request.app.state.metrics.increment("upload_face.rate_limited")
        raise

    stored_images: list[dict] = []
    queued_backfills = 0
    backfill_jobs: list[dict] = []
    updated_user = current_user
    for file in files:
        stored = await _store_reference_image(
            request,
            current_user=current_user,
            file=file,
        )
        updated_user = stored["updated_user"]
        stored_images.append(stored["reference_image"])
        backfill_job = _queue_user_backfill(
            request,
            current_user=current_user,
            reference_image_record=stored["reference_image_record"],
        )
        backfill_jobs.append(backfill_job)
        if backfill_job["queued"]:
            queued_backfills += 1

    request.app.state.metrics.increment("upload_face.success")
    logger.info(
        "Reference images uploaded: user_id=%s uploaded=%s embeddings_count=%s",
        current_user["user_id"],
        len(stored_images),
        len(updated_user.get("embeddings", [])),
    )
    return {
        "user_id": current_user["user_id"],
        "uploaded": len(stored_images),
        "embeddings_count": len(updated_user.get("embeddings", [])),
        "backfill_jobs_queued": queued_backfills,
        "backfill_job_ids": [
            job["job_id"] for job in backfill_jobs if job.get("queued") and job.get("job_id")
        ],
        "backfill_jobs": backfill_jobs,
        "backfill_status": _summarize_backfill_status(
            request,
            current_user=current_user,
            job_ids={
                job["job_id"]
                for job in backfill_jobs
                if job.get("queued") and job.get("job_id")
            },
        ),
        "reference_images": stored_images,
        "message": "Reference images uploaded successfully",
    }


@router.delete("/me/reference-images/{reference_image_id}")
def delete_reference_image(
    reference_image_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> dict:
    deleted = request.app.state.db.delete_user_embedding(
        user_id=current_user["user_id"],
        embedding_id=reference_image_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Reference image not found")
    return {
        "reference_image_id": reference_image_id,
        "message": "Reference image deleted",
    }


@router.post("/users/upload-face")
async def upload_face(
    request: Request,
    current_user: dict = Depends(get_current_user),
    file: UploadFile = File(...),
) -> dict:
    if not current_user.get("pool_id"):
        raise HTTPException(status_code=400, detail="Select a pool before uploading reference images")

    client_ip = request.client.host if request.client else "unknown"
    request.app.state.metrics.increment("upload_face.attempt")
    try:
        request.app.state.upload_rate_limiter.check(f"{current_user['user_id']}:{client_ip}")
    except HTTPException:
        request.app.state.metrics.increment("upload_face.rate_limited")
        raise

    stored = await _store_reference_image(
        request,
        current_user=current_user,
        file=file,
    )
    updated_user = stored["updated_user"]
    backfill_job = _queue_user_backfill(
        request,
        current_user=current_user,
        reference_image_record=stored["reference_image_record"],
    )

    request.app.state.metrics.increment("upload_face.success")
    logger.info(
        "Face uploaded: user_id=%s embeddings_count=%s",
        current_user["user_id"],
        len(updated_user.get("embeddings", [])),
    )
    return {
        "user_id": current_user["user_id"],
        "embeddings_count": len(updated_user.get("embeddings", [])),
        "backfill_queued": backfill_job["queued"],
        "backfill_job_id": backfill_job["job_id"],
        "backfill_job": backfill_job,
        "backfill_status": _summarize_backfill_status(
            request,
            current_user=current_user,
            job_ids={backfill_job["job_id"]} if backfill_job.get("job_id") else set(),
        ),
        "message": "Face uploaded successfully",
    }


@router.get("/user/videos")
def get_user_videos(
    request: Request,
    group_by_video: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    request.app.state.metrics.increment("user_videos.fetch")
    matches = request.app.state.db.list_matches_for_user(
        current_user["user_id"],
        pool_id=current_user.get("pool_id"),
    )
    media = request.app.state.media_service

    videos = [
        {
            "track_id": match.get("track_id"),
            "video_id": match.get("video_id") or _video_id_from_source(match.get("source_video_s3")),
            "keyframe": media.get_presigned_url(match.get("keyframe_s3") or match.get("keyframe"))
            or media.get_presigned_url(key=f"thumbnails/{match.get('track_id')}.jpg"),
            "timestamp": match.get("timestamp"),
            "confidence": float(match.get("confidence", 0.0)),
            "score": float(match.get("score", 0.0)),
            "download_url": media.get_presigned_url(key=f"rides/{match.get('track_id')}.mp4"),
            "preview_url": media.get_presigned_url(key=f"previews/{match.get('track_id')}.mp4"),
            "source_video_s3": match.get("source_video_s3"),
            "source_video_url": media.get_presigned_url(match.get("source_video_s3")),
        }
        for match in matches
    ]

    if not group_by_video:
        logger.info("Fetched videos: user_id=%s count=%s", current_user["user_id"], len(videos))
        return videos

    grouped: dict[str, list[dict]] = defaultdict(list)
    for video in videos:
        grouped[video.get("video_id") or "unknown"].append(video)

    return [
        {
            "video_id": video_id,
            "matches": items,
        }
        for video_id, items in grouped.items()
    ]


def _video_id_from_source(source_video_s3: str | None) -> str | None:
    if not source_video_s3:
        return None

    filename = source_video_s3.rstrip("/").split("/")[-1]
    return os.path.splitext(filename)[0] or None
