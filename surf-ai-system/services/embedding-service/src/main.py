import json
import os
import sys
import time
from datetime import datetime

import boto3
import cv2

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from src.aggregator import EmbeddingAggregator
from src.config import config
from src.embedder import FaceEmbedder
from src.face_detector import FaceDetector
from shared.utils.face_preprocessing import preprocess_face, summarize_face_tensor
from shared.utils.logger import get_logger
from shared.utils.pipeline_store import PipelineStore
from shared.utils.system_config import SystemConfigService
from shared.utils.worker_safety import (
    GracefulShutdown,
    WorkerLeaseGuard,
    WorkerRuntimeStats,
    get_receive_count,
    send_to_dlq,
    worker_instance_id,
)

logger = get_logger("embedding-service")
WORKER_TYPE = "embedding-service"
sqs_client = boto3.client("sqs", region_name=config.aws_region)
s3_client = boto3.client("s3", region_name=config.aws_region)
pipeline_store = PipelineStore(os.environ.get("SQLITE_DB_PATH", "/app/data/surf_ai.db"))
system_config = SystemConfigService(pipeline_store.db_path)
_LAST_RETENTION_CLEANUP_AT = 0.0


def _record_worker_metric(name: str, value: int = 1) -> None:
    pipeline_store.increment_metric(f"worker.{WORKER_TYPE}.{name}", value)


def _log_worker_stats(stats: WorkerRuntimeStats) -> None:
    logger.info("Worker metrics snapshot: %s", stats.snapshot())


def _embedding_job_key(msg_body: dict) -> str:
    if msg_body.get("idempotency_key"):
        return str(msg_body["idempotency_key"])
    return (
        f"embedding:{msg_body.get('video_id') or msg_body.get('source_video_id')}:"
        f"{msg_body.get('track_id')}:{msg_body.get('start_time') or msg_body.get('keyframe_s3') or 'root'}"
    )


def download_image(s3_path: str, local_path: str) -> bool:
    bucket = s3_path.split("//")[1].split("/")[0]
    key = s3_path.split(bucket + "/")[1]
    try:
        s3_client.download_file(bucket, key, local_path)
        return True
    except Exception as exc:
        logger.error("Failed to download %s: %s", s3_path, exc)
        return False


def compute_quality_score(det_score, face_size, blur_score):
    confidence_component = min(max(float(det_score), 0.0), 1.0)
    size_component = min(max(float(face_size), 0.0) / max(float(config.min_face_size) * 2.0, 1.0), 1.0)
    blur_component = min(max(float(blur_score), 0.0) / max(float(config.min_blur_score) * 2.0, 1.0), 1.0)
    return float((confidence_component * 0.45) + (size_component * 0.35) + (blur_component * 0.20))


def quality_rejection_reason(*, det_score, face_size, blur_score, pose_ok, quality_score, min_quality_score):
    if face_size < config.min_face_size:
        return "small_face"
    if float(det_score) < config.min_confidence:
        return "low_detection_confidence"
    if blur_score < config.min_blur_score:
        return "blurry_face"
    if not pose_ok:
        return "bad_pose"
    if float(quality_score) < float(min_quality_score):
        return "low_quality_score"
    return None


def runtime_embedding_settings() -> dict[str, int | float]:
    return {
        "min_frames_per_track": int(
            system_config.get_config("min_frames_per_track", config.matching_min_track_embeddings)
        ),
        "min_track_consistency": float(
            os.environ.get("MIN_TRACK_CONSISTENCY", str(config.min_track_consistency))
        ),
        "top_k_embeddings": int(
            system_config.get_config("top_k_embeddings", config.track_top_k)
        ),
        "min_quality_score": float(
            system_config.get_config("min_quality_score", config.min_quality_score)
        ),
        "retention_days": int(
            system_config.get_config("retention_days", config.retention_days)
        ),
    }


def maybe_cleanup_storage(force: bool = False):
    global _LAST_RETENTION_CLEANUP_AT
    now = time.time()
    if not force and (now - _LAST_RETENTION_CLEANUP_AT) < config.cleanup_interval_seconds:
        return None
    settings = runtime_embedding_settings()
    retention_days = int(settings["retention_days"])
    cleanup_summary = pipeline_store.cleanup_expired_artifacts(
        retention_days=retention_days,
        debug_retention_days=min(config.debug_retention_days, retention_days),
    )
    for s3_path in cleanup_summary.get("deleted_debug_image_s3_paths", []):
        try:
            bucket = s3_path.split("//", 1)[1].split("/", 1)[0]
            key = s3_path.split(bucket + "/", 1)[1]
            s3_client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            logger.warning("Failed to delete expired debug frame asset %s: %s", s3_path, exc)
    _LAST_RETENTION_CLEANUP_AT = now
    logger.info("Artifact retention cleanup completed: %s", cleanup_summary)
    return cleanup_summary


def update_video_embedding_diagnostics(
    video_id: str | None,
    *,
    tracks_received_increment: int = 0,
    tracks_with_embeddings_increment: int = 0,
    tracks_rejected_increment: int = 0,
    tracks_without_faces_increment: int = 0,
    tracks_below_matching_threshold_increment: int = 0,
    valid_faces_detected_increment: int = 0,
    last_track_id: str | None = None,
    last_confidence: float | None = None,
    stage_started_at: str | None = None,
    stage_completed_at: str | None = None,
    processing_seconds: float | None = None,
    track_patch: dict | None = None,
    rejection_reason: str | None = None,
):
    if not video_id:
        return

    existing = pipeline_store.get_video(video_id)
    if not existing:
        return

    diagnostics = existing.get("diagnostics") or {}
    embedding_data = diagnostics.get("embedding_service") or {}
    frame_data = diagnostics.get("frame_processor") or {}
    rejection_counts = dict(embedding_data.get("rejection_counts") or {})
    if rejection_reason:
        rejection_counts[rejection_reason] = int(rejection_counts.get(rejection_reason, 0)) + 1

    track_state = dict(embedding_data.get("tracks") or {})
    if last_track_id and track_patch is not None:
        track_state[str(last_track_id)] = {
            **(track_state.get(str(last_track_id)) or {}),
            **track_patch,
        }

    embedding_started_at = embedding_data.get("started_at") or stage_started_at
    frame_patch = None
    if frame_data.get("started_at") and not frame_data.get("completed_at") and embedding_started_at:
        fallback_processing_seconds = frame_data.get("processing_seconds")
        if fallback_processing_seconds is None:
            try:
                fallback_processing_seconds = round(
                    max(
                        (
                            datetime.fromisoformat(embedding_started_at)
                            - datetime.fromisoformat(frame_data["started_at"])
                        ).total_seconds(),
                        0.0,
                    ),
                    3,
                )
            except ValueError:
                fallback_processing_seconds = None
        frame_patch = {
            "completed_at": embedding_started_at,
            "processing_seconds": fallback_processing_seconds,
            "output_tracks": max(
                int(frame_data.get("output_tracks", 0) or 0),
                int(embedding_data.get("tracks_received", 0)) + tracks_received_increment,
            ),
        }

    patch = {
        "embedding_service": {
            "started_at": embedding_started_at,
            "completed_at": stage_completed_at or embedding_data.get("completed_at"),
            "processing_seconds": processing_seconds
            if processing_seconds is not None
            else embedding_data.get("processing_seconds"),
            "tracks_received": int(embedding_data.get("tracks_received", 0)) + tracks_received_increment,
            "tracks_with_embeddings": int(embedding_data.get("tracks_with_embeddings", 0))
            + tracks_with_embeddings_increment,
            "tracks_rejected": int(embedding_data.get("tracks_rejected", 0))
            + tracks_rejected_increment,
            "tracks_without_faces": int(embedding_data.get("tracks_without_faces", 0))
            + tracks_without_faces_increment,
            "tracks_below_matching_threshold": int(
                embedding_data.get("tracks_below_matching_threshold", 0)
            )
            + tracks_below_matching_threshold_increment,
            "valid_faces_detected": int(embedding_data.get("valid_faces_detected", 0))
            + valid_faces_detected_increment,
            "last_track_id": last_track_id or embedding_data.get("last_track_id"),
            "last_confidence": last_confidence
            if last_confidence is not None
            else embedding_data.get("last_confidence"),
            "rejection_counts": rejection_counts,
            "tracks": track_state,
            "updated_at": datetime.utcnow().isoformat(),
        },
    }
    if frame_patch is not None:
        patch["frame_processor"] = frame_patch
    pipeline_store.update_video_diagnostics(video_id, patch)


def _record_embedding_metric(name: str, value: int = 1) -> None:
    pipeline_store.increment_metric(name, value)


def finalize_video_if_resolved(video_id: str | None) -> None:
    if not video_id:
        return

    video = pipeline_store.get_video(video_id)
    if not video or video.get("status") == "failed":
        return

    diagnostics = video.get("diagnostics") or {}
    frame_data = diagnostics.get("frame_processor") or {}
    expected_tracks = int(frame_data.get("output_tracks", 0) or 0)
    if expected_tracks <= 0:
        if frame_data.get("completed_at"):
            pipeline_store.update_video_status(video_id, "completed")
        return

    embedding_tracks = (diagnostics.get("embedding_service") or {}).get("tracks") or {}
    matching_tracks = (diagnostics.get("matching_service") or {}).get("tracks") or {}
    rejected_tracks = sum(
        1 for item in embedding_tracks.values() if item.get("status") == "rejected"
    )
    resolved_tracks = len(matching_tracks) + rejected_tracks
    if resolved_tracks >= expected_tracks:
        pipeline_store.update_video_status(video_id, "completed")


def _frame_sources_from_message(msg_body: dict) -> list[dict]:
    debug_frames = msg_body.get("debug_frames") or []
    if debug_frames:
        return [
            {
                "frame_index": int(frame.get("frame_index", index)),
                "frame_timestamp": frame.get("frame_timestamp"),
                "image_s3": frame.get("image_s3"),
                "bbox": frame.get("bbox"),
            }
            for index, frame in enumerate(debug_frames)
        ]

    keyframes = msg_body.get("keyframes", [])
    if msg_body.get("keyframe_s3"):
        keyframes.append(msg_body["keyframe_s3"])
    return [
        {
            "frame_index": index,
            "frame_timestamp": None,
            "image_s3": s3_path,
            "bbox": None,
        }
        for index, s3_path in enumerate(list(dict.fromkeys(keyframes)))
    ]


def _extract_face_record(
    *,
    img,
    faces,
    frame_index: int,
    detector: FaceDetector,
    embedder: FaceEmbedder,
    min_quality_score: float,
):
    if not faces:
        return None

    candidates = []
    for face in faces:
        x1, y1, x2, y2 = [int(value) for value in face.bbox]
        face_size = max(x2 - x1, y2 - y1)
        blur = detector.get_blur_score(img, face.bbox)
        pose_ok = detector.check_pose(face, config.max_yaw, config.max_pitch)
        quality = compute_quality_score(face.det_score, face_size, blur)
        rejection_reason = quality_rejection_reason(
            det_score=face.det_score,
            face_size=face_size,
            blur_score=blur,
            pose_ok=pose_ok,
            quality_score=quality,
            min_quality_score=min_quality_score,
        )
        eligible = rejection_reason is None

        try:
            embedding = embedder.extract_embedding(face)
        except Exception:
            continue

        candidates.append(
            {
                "face": face,
                "embedding": embedding.astype(float).tolist(),
                "quality_score": quality,
                "det_score": float(face.det_score),
                "face_size": float(face_size),
                "blur_score": float(blur),
                "source_frame_index": frame_index,
                "eligible_for_aggregation": eligible,
                "rejection_reason": rejection_reason,
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda item: item["quality_score"])


def process_track(
    msg_body: dict,
    detector: FaceDetector,
    embedder: FaceEmbedder,
    aggregator_template: EmbeddingAggregator,
):
    track_started_at = datetime.utcnow().isoformat()
    track_started_monotonic = time.time()
    track_id = str(msg_body.get("track_id"))
    camera_id = msg_body.get("camera_id")
    pool_id = msg_body.get("pool_id")
    video_id = msg_body.get("video_id") or msg_body.get("source_video_id")
    frame_sources = _frame_sources_from_message(msg_body)
    frames_received = len(frame_sources)
    settings = runtime_embedding_settings()
    min_frames_per_track = int(settings["min_frames_per_track"])
    min_track_consistency = float(settings["min_track_consistency"])
    min_quality_score = float(settings["min_quality_score"])
    top_k_embeddings = int(settings["top_k_embeddings"])
    aggregator = EmbeddingAggregator(
        max_similarity=aggregator_template.max_similarity,
        min_samples=min_frames_per_track,
        min_quality_score=min_quality_score,
        top_k=top_k_embeddings,
    )

    update_video_embedding_diagnostics(
        video_id,
        tracks_received_increment=1,
        last_track_id=track_id,
        stage_started_at=track_started_at,
    )
    _record_embedding_metric("embedding.tracks_received")

    if not frame_sources:
        logger.info("[%s] No track frames available for face embedding", track_id)
        processing_seconds = round(time.time() - track_started_monotonic, 3)
        update_video_embedding_diagnostics(
            video_id,
            tracks_rejected_increment=1,
            tracks_without_faces_increment=1,
            last_track_id=track_id,
            stage_completed_at=datetime.utcnow().isoformat(),
            processing_seconds=processing_seconds,
            track_patch={
                "status": "rejected",
                "rejection_reason": "too_few_frames",
                "frames_received": frames_received,
                "frames_processed": 0,
                "embeddings_created": 0,
                "used_frames_count": 0,
                "quality_avg": 0.0,
                "consistency": None,
                "processed_at": datetime.utcnow().isoformat(),
            },
            rejection_reason="too_few_frames",
        )
        _record_embedding_metric("embedding.rejected.too_few_frames")
        finalize_video_if_resolved(video_id)
        return
    if len(frame_sources) < min_frames_per_track:
        logger.info(
            "[%s] Ignoring track with too few frames. frames_received=%s min_required=%s",
            track_id,
            len(frame_sources),
            min_frames_per_track,
        )
        processing_seconds = round(time.time() - track_started_monotonic, 3)
        update_video_embedding_diagnostics(
            video_id,
            tracks_rejected_increment=1,
            tracks_below_matching_threshold_increment=1,
            last_track_id=track_id,
            stage_completed_at=datetime.utcnow().isoformat(),
            processing_seconds=processing_seconds,
            track_patch={
                "status": "rejected",
                "rejection_reason": "too_few_frames",
                "frames_received": frames_received,
                "frames_processed": 0,
                "embeddings_created": 0,
                "used_frames_count": 0,
                "quality_avg": 0.0,
                "consistency": None,
                "processed_at": datetime.utcnow().isoformat(),
            },
            rejection_reason="too_few_frames",
        )
        _record_embedding_metric("embedding.rejected.too_few_frames")
        finalize_video_if_resolved(video_id)
        return

    faces_data = []
    frames_processed = 0
    debug_records: dict[int, dict] = {}

    for frame_source in frame_sources:
        frame_index = int(frame_source["frame_index"])
        s3_path = frame_source.get("image_s3")
        if not s3_path:
            continue

        local_path = f"/tmp/{camera_id or 'video'}_{track_id}_{frame_index}.jpg"
        if not download_image(s3_path, local_path):
            continue

        try:
            img = cv2.imread(local_path)
            if img is None:
                continue

            frames_processed += 1
            faces = detector.detect(img)
            frame_record = {
                "frame_index": frame_index,
                "frame_timestamp": frame_source.get("frame_timestamp"),
                "image_s3": s3_path,
                "bbox": frame_source.get("bbox"),
                "has_face": bool(faces),
                "face_bbox": None,
                "embedding": None,
                "quality_score": None,
                "det_score": None,
                "face_size": None,
                "blur_score": None,
                "rejection_reason": "no_face_detected" if not faces else None,
                "is_valid": False,
                "used_for_embedding": False,
            }

            best_face_record = _extract_face_record(
                img=img,
                faces=faces,
                frame_index=frame_index,
                detector=detector,
                embedder=embedder,
                min_quality_score=min_quality_score,
            )
            if best_face_record is not None:
                processed_face = preprocess_face(
                    img,
                    bbox=best_face_record["face"].bbox,
                    kps=getattr(best_face_record["face"], "kps", None),
                )
                print({"stage": "embedding_input", **summarize_face_tensor(processed_face)})

                frame_record["face_bbox"] = [
                    float(value) for value in best_face_record["face"].bbox
                ]
                frame_record["embedding"] = best_face_record["embedding"]
                frame_record["quality_score"] = float(best_face_record["quality_score"])
                frame_record["det_score"] = float(best_face_record["det_score"])
                frame_record["face_size"] = float(best_face_record["face_size"])
                frame_record["blur_score"] = float(best_face_record["blur_score"])
                frame_record["rejection_reason"] = best_face_record.get("rejection_reason")
                frame_record["is_valid"] = bool(best_face_record["eligible_for_aggregation"])

                if best_face_record["eligible_for_aggregation"]:
                    faces_data.append(best_face_record)

            debug_records[frame_index] = frame_record
            if video_id:
                pipeline_store.upsert_video_debug_frame(
                    video_id=video_id,
                    track_id=track_id,
                    frame_index=frame_index,
                    frame_timestamp=frame_source.get("frame_timestamp"),
                    image_s3=s3_path,
                    bbox=frame_source.get("bbox"),
                    face_bbox=frame_record["face_bbox"],
                    embedding=frame_record["embedding"],
                    quality_score=frame_record["quality_score"],
                    det_score=frame_record["det_score"],
                    face_size=frame_record["face_size"],
                    blur_score=frame_record["blur_score"],
                    rejection_reason=frame_record["rejection_reason"],
                    has_face=frame_record["has_face"],
                    is_valid=frame_record["is_valid"],
                    used_for_embedding=False,
                )
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

    embeddings_created = len(faces_data)
    evaluation = aggregator.evaluate(
        faces_data,
        min_consistency=min_track_consistency,
    )
    aggregation_result = evaluation["result"]
    logger.info(
        {
            "track_id": track_id,
            "frames_received": frames_received,
            "frames_processed": frames_processed,
            "frame_embeddings_created": embeddings_created,
        }
    )

    if aggregation_result is None:
        rejection_reason = str(evaluation["rejection_reason"] or "low_quality_score")
        processing_seconds = round(time.time() - track_started_monotonic, 3)
        update_video_embedding_diagnostics(
            video_id,
            tracks_rejected_increment=1,
            tracks_without_faces_increment=1 if embeddings_created == 0 else 0,
            tracks_below_matching_threshold_increment=1 if embeddings_created > 0 else 0,
            valid_faces_detected_increment=embeddings_created,
            last_track_id=track_id,
            stage_completed_at=datetime.utcnow().isoformat(),
            processing_seconds=processing_seconds,
            track_patch={
                "status": "rejected",
                "rejection_reason": rejection_reason,
                "frames_received": frames_received,
                "frames_processed": frames_processed,
                "embeddings_created": embeddings_created,
                "used_frames_count": int((evaluation.get("details") or {}).get("used_frames_count", 0) or 0),
                "quality_avg": (evaluation.get("details") or {}).get("quality_avg"),
                "consistency": (evaluation.get("details") or {}).get("consistency"),
                "processed_at": datetime.utcnow().isoformat(),
            },
            rejection_reason=rejection_reason,
        )
        _record_embedding_metric(f"embedding.rejected.{rejection_reason}")
        logger.info(
            "[%s] Skipping matching. frame_embeddings=%s reason=%s min_required=%s",
            track_id,
            embeddings_created,
            rejection_reason,
            min_frames_per_track,
        )
        finalize_video_if_resolved(video_id)
        return

    used_frame_indexes = set(aggregation_result["used_frame_indexes"])
    video_embedding_record = None
    if video_id:
        video_embedding_record = pipeline_store.upsert_video_embedding(
            video_id=video_id,
            track_id=track_id,
            camera_id=camera_id,
            pool_id=pool_id,
            embedding=aggregation_result["embedding"],
            frames_count=aggregation_result["used_frames_count"],
            frames_received=frames_received,
            embeddings_created=embeddings_created,
            confidence=float(aggregation_result["confidence"]),
            consistency=float(aggregation_result["consistency"]),
            quality_avg=float(aggregation_result["quality_avg"]),
            aggregation_method=aggregation_result["aggregation_method"],
            keyframe_s3=msg_body.get("keyframe_s3"),
            start_time=msg_body.get("start_time"),
            end_time=msg_body.get("end_time"),
        )

        for face_data in faces_data:
            frame_index = int(face_data["source_frame_index"])
            debug_record = debug_records.get(frame_index) or {}
            pipeline_store.upsert_video_frame_embedding(
                video_id=video_id,
                track_id=track_id,
                frame_index=frame_index,
                frame_timestamp=debug_record.get("frame_timestamp"),
                pool_id=pool_id,
                embedding=face_data["embedding"],
                quality_score=float(face_data["quality_score"]),
                video_embedding_id=video_embedding_record["video_embedding_id"],
                used_for_track_embedding=frame_index in used_frame_indexes,
            )
            pipeline_store.upsert_video_debug_frame(
                video_id=video_id,
                track_id=track_id,
                frame_index=frame_index,
                video_embedding_id=video_embedding_record["video_embedding_id"],
                frame_timestamp=debug_record.get("frame_timestamp"),
                image_s3=debug_record.get("image_s3"),
                bbox=debug_record.get("bbox"),
                face_bbox=debug_record.get("face_bbox"),
                embedding=debug_record.get("embedding"),
                quality_score=debug_record.get("quality_score"),
                det_score=debug_record.get("det_score"),
                face_size=debug_record.get("face_size"),
                blur_score=debug_record.get("blur_score"),
                rejection_reason=debug_record.get("rejection_reason"),
                has_face=debug_record.get("has_face", False),
                is_valid=debug_record.get("is_valid", False),
                used_for_embedding=frame_index in used_frame_indexes,
            )
        prune_summary = pipeline_store.prune_track_frame_embeddings(
            video_id=video_id,
            track_id=track_id,
            keep_top_n=top_k_embeddings,
            min_quality_score=min_quality_score,
        )
        if prune_summary["deleted_low_quality"] or prune_summary["deleted_overflow"]:
            logger.info("[%s] Pruned stored frame embeddings: %s", track_id, prune_summary)
        maybe_cleanup_storage()

    update_video_embedding_diagnostics(
        video_id,
        tracks_with_embeddings_increment=1 if (video_embedding_record and video_embedding_record.get("created")) else 0,
        valid_faces_detected_increment=aggregation_result["used_frames_count"],
        last_track_id=track_id,
        last_confidence=float(aggregation_result["confidence"]),
        stage_completed_at=datetime.utcnow().isoformat(),
        processing_seconds=round(time.time() - track_started_monotonic, 3),
        track_patch={
            "status": "accepted",
            "rejection_reason": None,
            "frames_received": frames_received,
            "frames_processed": frames_processed,
            "embeddings_created": embeddings_created,
            "used_frames_count": aggregation_result["used_frames_count"],
            "quality_avg": float(aggregation_result["quality_avg"]),
            "consistency": float(aggregation_result["consistency"]),
            "confidence": float(aggregation_result["confidence"]),
            "processed_at": datetime.utcnow().isoformat(),
        },
    )
    _record_embedding_metric("embedding.accepted")

    output_data = {
        "job_type": "track_embedding_match",
        "idempotency_key": (
            f"matching:{video_id or msg_body.get('source_video_id')}:{track_id}:"
            f"{None if video_embedding_record is None else video_embedding_record['video_embedding_id'] or 'root'}"
        ),
        "track_id": track_id,
        "camera_id": camera_id,
        "pool_id": pool_id,
        "video_id": msg_body.get("video_id"),
        "source_video_id": msg_body.get("source_video_id"),
        "source_video_s3": msg_body.get("source_video_s3"),
        "keyframe_s3": msg_body.get("keyframe_s3"),
        "start_time": msg_body.get("start_time"),
        "end_time": msg_body.get("end_time"),
        "video_embedding_id": None if video_embedding_record is None else video_embedding_record["video_embedding_id"],
        "track_embedding": aggregation_result["embedding"],
        "face_embedding": aggregation_result["embedding"],
        "embedding_confidence": float(aggregation_result["confidence"]),
        "num_faces_detected": aggregation_result["used_frames_count"],
        "num_embeddings": aggregation_result["used_frames_count"],
        "frames_count": aggregation_result["used_frames_count"],
        "frames_received": frames_received,
        "embeddings_created": embeddings_created,
        "avg_quality": float(aggregation_result["quality_avg"]),
        "consistency": float(aggregation_result["consistency"]),
        "aggregation_method": aggregation_result["aggregation_method"],
        "used_frame_indexes": aggregation_result["used_frame_indexes"],
    }

    sqs_client.send_message(
        QueueUrl=config.output_sqs_url,
        MessageBody=json.dumps(output_data),
    )

    logger.info(
        "[%s] Output track embedding. frames_processed=%s frame_embeddings=%s frames_used=%s confidence=%.3f",
        track_id,
        frames_processed,
        embeddings_created,
        aggregation_result["used_frames_count"],
        float(aggregation_result["confidence"]),
    )
    finalize_video_if_resolved(video_id)


def main():
    logger.info("Starting Embedding Service Orchestration Worker")

    detector = FaceDetector(model_name=config.model_name, ctx_id=config.ctx_id)
    embedder = FaceEmbedder()
    aggregator = EmbeddingAggregator(
        max_similarity=config.max_similarity,
        min_samples=config.matching_min_track_embeddings,
        min_quality_score=config.min_quality_score,
        top_k=config.track_top_k,
    )
    shutdown = GracefulShutdown(logger=logger, worker_name=WORKER_TYPE)
    leader_id = worker_instance_id(WORKER_TYPE)
    stats = WorkerRuntimeStats(WORKER_TYPE)
    lease_guard = WorkerLeaseGuard(
        pipeline_store=pipeline_store,
        worker_type=WORKER_TYPE,
        leader_id=leader_id,
        ttl_seconds=config.worker_lease_ttl_seconds,
        metadata={"queue_url": config.input_sqs_url},
        logger=logger,
    )

    while not shutdown.should_stop():
        try:
            if lease_guard.lease_lost():
                logger.warning("Embedding worker lease lost; waiting before retrying leadership")
                shutdown.wait(2)
                continue

            if not lease_guard.ensure_acquired():
                shutdown.wait(2)
                continue

            response = sqs_client.receive_message(
                QueueUrl=config.input_sqs_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                AttributeNames=["All"],
            )

            messages = response.get("Messages", [])
            for message in messages:
                if shutdown.should_stop():
                    break

                receipt_handle = message["ReceiptHandle"]
                receive_count = get_receive_count(message)

                try:
                    body = json.loads(message["Body"])
                except json.JSONDecodeError as exc:
                    logger.error("Invalid embedding message JSON: %s", exc)
                    stats.record_failure()
                    _record_worker_metric("failures")
                    send_to_dlq(
                        sqs_client=sqs_client,
                        dlq_url=config.dlq_sqs_url,
                        worker_type=WORKER_TYPE,
                        message=message,
                        payload=None,
                        reason="invalid_json",
                        error_message=str(exc),
                    )
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle,
                    )
                    continue

                job_key = _embedding_job_key(body)
                if not pipeline_store.try_start_job(
                    job_type="embedding_track",
                    job_key=job_key,
                    job_id=message.get("MessageId"),
                    payload=body,
                ):
                    logger.info("Skipping duplicate embedding job job_key=%s", job_key)
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle,
                    )
                    _record_worker_metric("duplicates")
                    continue

                try:
                    process_track(body, detector, embedder, aggregator)
                    pipeline_store.finish_job(job_key=job_key, status="completed")
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle,
                    )
                    stats.record_processed()
                    _record_worker_metric("messages_processed")
                    if stats.processed % config.metrics_log_interval == 0:
                        _log_worker_stats(stats)
                except Exception as exc:
                    pipeline_store.finish_job(
                        job_key=job_key,
                        status="failed",
                        error_message=str(exc),
                    )
                    logger.error("Error processing message payload internally: %s", exc, exc_info=True)
                    stats.record_failure()
                    _record_worker_metric("failures")
                    if receive_count >= config.max_receive_count:
                        sent_to_dlq = send_to_dlq(
                            sqs_client=sqs_client,
                            dlq_url=config.dlq_sqs_url,
                            worker_type=WORKER_TYPE,
                            message=message,
                            payload=body,
                            reason="max_receive_count_exceeded",
                            error_message=str(exc),
                        )
                        if sent_to_dlq:
                            stats.record_dead_letter()
                            _record_worker_metric("dead_lettered")
                        logger.error(
                            "Dead-lettering embedding job job_key=%s receive_count=%s sent_to_dlq=%s",
                            job_key,
                            receive_count,
                            sent_to_dlq,
                        )
                        sqs_client.delete_message(
                            QueueUrl=config.input_sqs_url,
                            ReceiptHandle=receipt_handle,
                        )
                    else:
                        stats.record_retry()
                        _record_worker_metric("retries")
                        logger.warning(
                            "Embedding job will be retried job_key=%s receive_count=%s",
                            job_key,
                            receive_count,
                        )

        except Exception as exc:
            logger.error("SQS Interface integration error offset: %s", exc, exc_info=True)
            shutdown.wait(5)
    try:
        lease_guard.release()
    except Exception as exc:
        logger.warning("Failed to release worker lease: %s", exc)
    _log_worker_stats(stats)


if __name__ == "__main__":
    main()
