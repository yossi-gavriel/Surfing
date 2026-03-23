import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field

from shared.utils.logger import get_logger
from shared.utils.embeddings import pairwise_cosine_similarity, pairwise_euclidean_distances
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


def _current_match_threshold() -> float:
    try:
        return float(os.environ.get("MATCH_THRESHOLD", "0.75"))
    except (TypeError, ValueError):
        return 0.75


def _build_compare_response(request: Request, *, video_id: str, user_id: str) -> dict[str, Any]:
    media = request.app.state.media_service
    user_embeddings = [
        {
            **item,
            "source_image_url": media.get_presigned_url(item.get("source_image_s3")),
        }
        for item in request.app.state.db.list_user_embeddings(user_id)
    ]
    video_embeddings = [
        {
            **item,
            "keyframe_url": media.get_presigned_url(item.get("keyframe_s3"))
            or media.get_presigned_url(key=f"thumbnails/{item.get('track_id')}.jpg"),
        }
        for item in request.app.state.pipeline_store.list_video_embeddings(video_id)
    ]
    debug_frames = [
        {
            **item,
            "image_url": media.get_presigned_url(item.get("image_s3")),
        }
        for item in request.app.state.pipeline_store.list_video_debug_frames(video_id)
    ]
    threshold = _current_match_threshold()

    comparisons: list[dict[str, Any]] = []
    video_frames: list[dict[str, Any]] = []
    debug_frame_results: list[dict[str, Any]] = []
    if user_embeddings and video_embeddings:
        user_vectors = [item["embedding"] for item in user_embeddings]
        video_vectors = [item["embedding"] for item in video_embeddings]
        distances = pairwise_euclidean_distances(video_vectors, user_vectors)
        similarities = pairwise_cosine_similarity(video_vectors, user_vectors)
        best_by_video_embedding: dict[str, dict[str, Any]] = {}

        for video_index, video_embedding in enumerate(video_embeddings):
            for user_index, user_embedding in enumerate(user_embeddings):
                distance = float(distances[video_index, user_index])
                similarity = float(similarities[video_index, user_index])
                comparison = {
                    "video_embedding_id": video_embedding["video_embedding_id"],
                    "user_embedding_id": user_embedding["user_embedding_id"],
                    "distance": distance,
                    "similarity": similarity,
                    "is_match_under_threshold": distance <= threshold,
                }
                comparisons.append(comparison)

                best_for_video = best_by_video_embedding.get(video_embedding["video_embedding_id"])
                if best_for_video is None or distance < best_for_video["distance"]:
                    best_by_video_embedding[video_embedding["video_embedding_id"]] = {
                        "video_embedding_id": video_embedding["video_embedding_id"],
                        "track_id": video_embedding["track_id"],
                        "keyframe_s3": video_embedding.get("keyframe_s3"),
                        "keyframe_url": video_embedding.get("keyframe_url"),
                        "start_time": video_embedding.get("start_time"),
                        "end_time": video_embedding.get("end_time"),
                        "best_user_embedding_id": user_embedding["user_embedding_id"],
                        "best_reference_image_url": user_embedding.get("source_image_url"),
                        "distance": distance,
                        "similarity": similarity,
                        "is_match_under_threshold": distance <= threshold,
                    }

        comparisons.sort(key=lambda item: item["distance"])
        video_frames = sorted(
            best_by_video_embedding.values(),
            key=lambda item: item["distance"],
        )
    else:
        video_frames = [
            {
                "video_embedding_id": item["video_embedding_id"],
                "track_id": item["track_id"],
                "keyframe_s3": item.get("keyframe_s3"),
                "keyframe_url": item.get("keyframe_url"),
                "start_time": item.get("start_time"),
                "end_time": item.get("end_time"),
                "best_user_embedding_id": None,
                "best_reference_image_url": None,
                "distance": None,
                "similarity": None,
                "is_match_under_threshold": False,
            }
            for item in video_embeddings
        ]

    if user_embeddings and debug_frames:
        debug_frame_items = [item for item in debug_frames if item.get("embedding") is not None]
        if debug_frame_items:
            user_vectors = [item["embedding"] for item in user_embeddings]
            frame_distances = pairwise_euclidean_distances(
                [item["embedding"] for item in debug_frame_items],
                user_vectors,
            )
            frame_similarities = pairwise_cosine_similarity(
                [item["embedding"] for item in debug_frame_items],
                user_vectors,
            )

            for frame_index, frame in enumerate(debug_frame_items):
                best_match_index = int(frame_distances[frame_index].argmin())
                debug_frame_results.append(
                    {
                        "debug_frame_id": frame["debug_frame_id"],
                        "track_id": frame["track_id"],
                        "frame_index": frame["frame_index"],
                        "frame_timestamp": frame.get("frame_timestamp"),
                        "image_url": frame.get("image_url"),
                        "bbox": frame.get("bbox"),
                        "face_bbox": frame.get("face_bbox"),
                        "has_face": frame.get("has_face", False),
                        "is_valid": frame.get("is_valid", False),
                        "used_for_embedding": frame.get("used_for_embedding", False),
                        "user_embedding_id": user_embeddings[best_match_index]["user_embedding_id"],
                        "distance": float(frame_distances[frame_index, best_match_index]),
                        "similarity": float(frame_similarities[frame_index, best_match_index]),
                        "is_match_under_threshold": float(frame_distances[frame_index, best_match_index]) <= threshold,
                    }
                )

        frames_without_embeddings = [item for item in debug_frames if item.get("embedding") is None]
        debug_frame_results.extend(
            [
                {
                    "debug_frame_id": frame["debug_frame_id"],
                    "track_id": frame["track_id"],
                    "frame_index": frame["frame_index"],
                    "frame_timestamp": frame.get("frame_timestamp"),
                    "image_url": frame.get("image_url"),
                    "bbox": frame.get("bbox"),
                    "face_bbox": frame.get("face_bbox"),
                    "has_face": frame.get("has_face", False),
                    "is_valid": frame.get("is_valid", False),
                    "used_for_embedding": frame.get("used_for_embedding", False),
                    "user_embedding_id": None,
                    "distance": None,
                    "similarity": None,
                    "is_match_under_threshold": False,
                }
                for frame in frames_without_embeddings
            ]
        )
    else:
        debug_frame_results = [
            {
                "debug_frame_id": frame["debug_frame_id"],
                "track_id": frame["track_id"],
                "frame_index": frame["frame_index"],
                "frame_timestamp": frame.get("frame_timestamp"),
                "image_url": frame.get("image_url"),
                "bbox": frame.get("bbox"),
                "face_bbox": frame.get("face_bbox"),
                "has_face": frame.get("has_face", False),
                "is_valid": frame.get("is_valid", False),
                "used_for_embedding": frame.get("used_for_embedding", False),
                "user_embedding_id": None,
                "distance": None,
                "similarity": None,
                "is_match_under_threshold": False,
            }
            for frame in debug_frames
        ]

    debug_frame_results.sort(
        key=lambda item: (
            item["track_id"],
            item["frame_index"],
        )
    )

    best_reference_image_url = None
    best_reference_user_embedding_id = None
    if comparisons:
        best_reference_user_embedding_id = comparisons[0]["user_embedding_id"]
        best_reference = next(
            (item for item in user_embeddings if item["user_embedding_id"] == best_reference_user_embedding_id),
            None,
        )
        best_reference_image_url = None if best_reference is None else best_reference.get("source_image_url")
    elif user_embeddings:
        latest_reference = next(
            (item for item in reversed(user_embeddings) if item.get("source_image_url")),
            None,
        )
        if latest_reference is not None:
            best_reference_user_embedding_id = latest_reference["user_embedding_id"]
            best_reference_image_url = latest_reference.get("source_image_url")

    return {
        "video_id": video_id,
        "user_embeddings": len(user_embeddings),
        "video_embeddings": len(video_embeddings),
        "comparisons": comparisons,
        "best_reference_user_embedding_id": best_reference_user_embedding_id,
        "best_reference_image_url": best_reference_image_url,
        "reference_images": [
            {
                "user_embedding_id": item["user_embedding_id"],
                "image_url": item.get("source_image_url"),
                "created_at": item.get("created_at"),
            }
            for item in user_embeddings
            if item.get("source_image_url")
        ],
        "video_frames": video_frames,
        "debug_frames": debug_frame_results,
        "summary": {
            "total_frames": len(debug_frame_results),
            "valid_frames": sum(1 for item in debug_frame_results if item.get("is_valid")),
            "best_similarity": None if not comparisons else comparisons[0]["similarity"],
            "best_distance": None if not comparisons else comparisons[0]["distance"],
            "force_match": bool(
                comparisons
                and comparisons[0]["similarity"] > 0.82
                and comparisons[0]["distance"] < threshold
            ),
        },
        "threshold": threshold,
    }


def _build_video_debug_summary(request: Request, *, video_id: str, user_id: str) -> dict[str, Any]:
    compare_response = _build_compare_response(request, video_id=video_id, user_id=user_id)
    best_match = compare_response["comparisons"][0] if compare_response["comparisons"] else None
    return {
        "user_embeddings_count": compare_response["user_embeddings"],
        "video_embeddings_count": compare_response["video_embeddings"],
        "min_distance": None if best_match is None else best_match["distance"],
        "best_similarity": None if best_match is None else best_match["similarity"],
        "threshold": compare_response["threshold"],
    }


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
    media = request.app.state.media_service
    videos = request.app.state.pipeline_store.list_videos()
    return [
        {
            **video,
            "source_video_url": media.get_presigned_url(video.get("s3_path")),
            **_build_video_debug_summary(
                request,
                video_id=video["video_id"],
                user_id=current_user["user_id"],
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
    video = request.app.state.pipeline_store.get_video(video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    return _build_compare_response(
        request,
        video_id=video_id,
        user_id=current_user["user_id"],
    )


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
