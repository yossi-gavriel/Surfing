from collections import defaultdict
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile

from shared.utils.logger import get_logger
from src.face_service import FaceUploadError
from src.security import get_current_user

logger = get_logger("api-users")

router = APIRouter()


@router.post("/users/upload-face")
async def upload_face(
    request: Request,
    current_user: dict = Depends(get_current_user),
    file: UploadFile = File(...),
) -> dict:
    client_ip = request.client.host if request.client else "unknown"
    request.app.state.metrics.increment("upload_face.attempt")
    try:
        request.app.state.upload_rate_limiter.check(f"{current_user['user_id']}:{client_ip}")
    except HTTPException:
        request.app.state.metrics.increment("upload_face.rate_limited")
        raise

    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is required")

    try:
        result = request.app.state.face_service.extract_embedding(image_bytes)
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

    request.app.state.metrics.increment("upload_face.success")
    logger.info(
        "Face uploaded: user_id=%s embeddings_count=%s",
        current_user["user_id"],
        len(updated_user.get("embeddings", [])),
    )
    return {
        "user_id": current_user["user_id"],
        "embeddings_count": len(updated_user.get("embeddings", [])),
        "message": "Face uploaded successfully",
    }


@router.get("/user/videos")
def get_user_videos(
    request: Request,
    group_by_video: bool = Query(False),
    current_user: dict = Depends(get_current_user),
):
    request.app.state.metrics.increment("user_videos.fetch")
    matches = request.app.state.db.list_matches_for_user(current_user["user_id"])
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
