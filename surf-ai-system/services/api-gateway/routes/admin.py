import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from shared.utils.logger import get_logger
from src.security import get_current_user, is_admin_email

logger = get_logger("api-admin")

router = APIRouter()


class CameraRequest(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    active: bool = True
    camera_id: str | None = None


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if not is_admin_email(current_user.get("email", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required",
        )
    return current_user


@router.post("/upload-video")
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(require_admin),
) -> dict:
    video_bytes = await file.read()
    if not video_bytes:
        raise HTTPException(status_code=400, detail="Video file is required")

    queue_url = request.app.state.admin_video_queue_url
    if not queue_url:
        raise HTTPException(status_code=500, detail="SQS queue is not configured")

    video_id = str(uuid.uuid4())
    _, extension = os.path.splitext(file.filename or "")
    object_key = f"uploads/videos/{video_id}{extension or '.mp4'}"

    try:
        s3_path = request.app.state.media_service.upload_bytes(
            data=video_bytes,
            key=object_key,
            content_type=file.content_type,
        )
        video_record = request.app.state.pipeline_store.create_video(
            video_id=video_id,
            s3_path=s3_path,
            status="uploaded",
        )

        request.app.state.admin_sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {
                    "video_id": video_id,
                    "s3_path": s3_path,
                    "type": "video",
                    "timestamp": video_record["created_at"],
                }
            ),
        )
    except Exception as exc:
        logger.error("Video upload failed for admin user %s: %s", current_user["user_id"], exc, exc_info=True)
        request.app.state.pipeline_store.update_video_status(
            video_id,
            "failed",
            error_message=str(exc),
        )
        raise HTTPException(status_code=500, detail="Unable to upload and queue video") from exc

    logger.info("Video queued: video_id=%s user_id=%s", video_id, current_user["user_id"])
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
    camera = request.app.state.pipeline_store.upsert_camera(
        camera_id=payload.camera_id,
        name=payload.name,
        url=payload.url,
        active=payload.active,
    )
    logger.info("Camera saved: camera_id=%s user_id=%s", camera["camera_id"], current_user["user_id"])
    return camera


@router.get("/cameras")
def list_cameras(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> list[dict]:
    _ = current_user
    return request.app.state.pipeline_store.list_cameras()


@router.get("/videos")
def list_videos(
    request: Request,
    current_user: dict = Depends(require_admin),
) -> list[dict]:
    _ = current_user
    media = request.app.state.media_service
    videos = request.app.state.pipeline_store.list_videos()
    return [
        {
            **video,
            "source_video_url": media.get_presigned_url(video.get("s3_path")),
        }
        for video in videos
    ]


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
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    queued_at = datetime.now(timezone.utc).isoformat()
    try:
        request.app.state.pipeline_store.update_video_status(video_id, "uploaded")
        request.app.state.admin_sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(
                {
                    "video_id": video["video_id"],
                    "s3_path": video["s3_path"],
                    "type": "video",
                    "timestamp": queued_at,
                }
            ),
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
