import json
import os
import time
from datetime import datetime, timedelta
from typing import Any

import boto3
import cv2
import redis

from services.frame_processor.src.config import config
from services.frame_processor.src.detector import PersonDetector
from services.frame_processor.src.frame_loader import extract_frames
from services.frame_processor.src.tracker import IoUTracker
from services.frame_processor.src.zones import ZoneCalculator
from shared.utils.logger import get_logger
from shared.utils.pipeline_store import PipelineStore

logger = get_logger("frame-processor")
sqs_client = boto3.client("sqs", region_name=config.aws_region)
s3_client = boto3.client("s3", region_name=config.aws_region)

try:
    redis_client = redis.Redis(host=config.redis_host, port=config.redis_port, db=0, decode_responses=True)
    redis_client.ping()
except Exception as exc:
    logger.warning("Redis initialization failed (%s). Proceeding without global cache.", exc)
    redis_client = None

try:
    pipeline_store = PipelineStore(os.environ.get("SQLITE_DB_PATH", "/app/data/surf_ai.db"))
except Exception as exc:
    logger.warning("Pipeline store unavailable (%s). Video status updates disabled.", exc)
    pipeline_store = None


def download_video(s3_path: str, local_path: str) -> None:
    bucket = s3_path.split("//", 1)[1].split("/", 1)[0]
    key = s3_path.split(bucket + "/", 1)[1]
    logger.info("Downloading %s to %s", s3_path, local_path)
    s3_client.download_file(bucket, key, local_path)


def is_uploaded_video_message(msg_body: dict[str, Any]) -> bool:
    return (msg_body.get("type") or "").lower() == "video"


def update_video_status(video_id: str | None, status: str, *, error_message: str | None = None) -> None:
    if not pipeline_store or not video_id:
        return
    try:
        pipeline_store.update_video_status(video_id, status, error_message=error_message)
    except Exception as exc:
        logger.warning("Failed to update video %s status to %s: %s", video_id, status, exc)


def source_context(msg_body: dict[str, Any]) -> dict[str, Any]:
    source_type = "video" if is_uploaded_video_message(msg_body) else "camera"
    s3_path = msg_body["s3_path"]
    filename = msg_body.get("file_name") or os.path.basename(s3_path)
    inferred_video_id = os.path.splitext(filename)[0]
    video_id = msg_body.get("video_id") or inferred_video_id
    camera_id = msg_body.get("camera_id")
    source_id = camera_id or video_id
    storage_key = camera_id or f"video-{video_id}"
    chunk_start_iso = msg_body.get("chunk_start") or msg_body.get("timestamp") or datetime.utcnow().isoformat()

    return {
        "source_type": source_type,
        "source_id": source_id,
        "storage_key": storage_key,
        "camera_id": camera_id,
        "video_id": video_id,
        "s3_path": s3_path,
        "filename": filename,
        "chunk_start_iso": chunk_start_iso,
    }


def process_chunk(msg_body: dict[str, Any]) -> None:
    start_time_profile = time.time()
    source = source_context(msg_body)
    if source["source_type"] == "video":
        update_video_status(source["video_id"], "processing")
        if pipeline_store and source["video_id"]:
            pipeline_store.set_video_diagnostics(
                source["video_id"],
                {
                    "frame_processor": {
                        "started_at": datetime.utcnow().isoformat(),
                        "source_video": source["s3_path"],
                        "sampled_frames": 0,
                        "detections": 0,
                        "tracks_seen": 0,
                        "output_tracks": 0,
                        "keyframes_uploaded": 0,
                    },
                    "embedding_service": {
                        "tracks_received": 0,
                        "tracks_with_embeddings": 0,
                        "tracks_without_faces": 0,
                        "tracks_below_matching_threshold": 0,
                        "valid_faces_detected": 0,
                    },
                },
            )

    local_path = f"/tmp/{source['filename']}"
    download_video(source["s3_path"], local_path)

    keyframe_dir = f"/tmp/keyframes/{source['storage_key']}"
    os.makedirs(keyframe_dir, exist_ok=True)

    detector = PersonDetector(
        model_name=config.model_name,
        min_confidence=config.min_confidence,
        inference_size=(config.inference_width, config.inference_height),
        min_bbox_area=config.min_bbox_area,
        max_aspect_ratio=config.max_aspect_ratio,
    )

    dt_iso = source["chunk_start_iso"].replace("Z", "+00:00")
    dt_chunk = datetime.fromisoformat(dt_iso)
    prefix_id = f"{source['storage_key']}_{dt_chunk.strftime('%Y%m%d_%H%M%S')}"

    tracker = IoUTracker(
        prefix_id=prefix_id,
        camera_id=source["storage_key"],
        redis_client=redis_client,
        iou_threshold=0.3,
        center_dist_threshold=config.center_dist_threshold,
        max_active=config.max_active_tracks,
        max_speed=config.max_velocity,
        conf_decay=config.conf_decay,
    )

    tracks_history: dict[Any, dict[str, Any]] = {}
    total_detections = 0
    sampled_frames = 0
    frame_width = None
    zone_calc = None

    for frame_idx, timestamp_sec, frame in extract_frames(local_path, config.frame_sample_rate):
        sampled_frames += 1
        if frame_width is None:
            frame_width = frame.shape[1]
            zone_calc = ZoneCalculator(frame_width)

        bboxes_info = detector.detect(frame)
        total_detections += len(bboxes_info)

        tracked_objects = tracker.update(bboxes_info)
        current_time_iso = (dt_chunk + timedelta(seconds=timestamp_sec)).isoformat()

        if config.debug_mode:
            debug_frame = frame.copy()

        for tid, bbox, conf in tracked_objects:
            if config.debug_mode:
                x1, y1, x2, y2 = [int(value) for value in bbox]
                cv2.rectangle(debug_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(
                    debug_frame,
                    f"{tid} {conf:.2f}",
                    (x1, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 0),
                    2,
                )

            if tid not in tracks_history:
                tracks_history[tid] = {
                    "camera_id": source["camera_id"],
                    "track_id": str(tid),
                    "video_id": source["video_id"],
                    "source_video_id": source["video_id"],
                    "source_video_s3": source["s3_path"],
                    "bboxes": [],
                    "frames": [],
                    "frame_timestamps": [],
                    "confidences": [],
                    "start_time": current_time_iso,
                    "end_time": current_time_iso,
                    "best_conf": 0.0,
                    "best_frame_crop": None,
                }

            tracks_history[tid]["bboxes"].append(bbox)
            tracks_history[tid]["frames"].append(frame_idx)
            tracks_history[tid]["frame_timestamps"].append(current_time_iso)
            tracks_history[tid]["confidences"].append(conf)
            tracks_history[tid]["end_time"] = current_time_iso

            if conf > tracks_history[tid]["best_conf"]:
                tracks_history[tid]["best_conf"] = conf
                bx1, by1, bx2, by2 = [int(value) for value in bbox]
                bx1, by1 = max(0, bx1), max(0, by1)
                bx2, by2 = min(frame.shape[1], bx2), min(frame.shape[0], by2)
                if bx2 > bx1 and by2 > by1:
                    tracks_history[tid]["best_frame_crop"] = frame[by1:by2, bx1:bx2].copy()

        if config.debug_mode:
            os.makedirs(config.debug_output_dir, exist_ok=True)
            cv2.imwrite(f"{config.debug_output_dir}/{source['storage_key']}_{source['filename']}_{frame_idx:04d}.jpg", debug_frame)

    os.remove(local_path)
    tracker.save_state()

    valid_tracks_count = 0
    uploaded_keyframes = 0
    for tid, data in tracks_history.items():
        if len(data["frames"]) < config.min_track_length:
            continue

        data["num_detections"] = len(data["frames"])
        data["duration_frames"] = len(data["frames"])
        data["avg_confidence"] = sum(data["confidences"]) / float(data["duration_frames"])
        data["max_confidence"] = max(data["confidences"])

        duration_weight = min(data["duration_frames"] / 15.0, 1.0)
        track_score = (data["avg_confidence"] * 0.4 + data["max_confidence"] * 0.6) * duration_weight
        data["track_score"] = float(track_score)
        if track_score < config.min_track_score:
            continue

        valid_tracks_count += 1

        first_bbox = data["bboxes"][0]
        last_bbox = data["bboxes"][-1]
        data["entry_zone"] = zone_calc.get_zone(first_bbox) if zone_calc else None
        data["exit_zone"] = zone_calc.get_zone(last_bbox) if zone_calc else None

        crop_img = data.pop("best_frame_crop", None)
        data.pop("best_conf", None)
        if crop_img is not None:
            keyframe_name = f"{tid}_{source['filename']}.jpg"
            keyframe_local = f"{keyframe_dir}/{keyframe_name}"
            cv2.imwrite(keyframe_local, crop_img)
            keyframe_s3 = f"keyframes/{source['storage_key']}/{keyframe_name}"
            s3_client.upload_file(keyframe_local, config.s3_bucket, keyframe_s3)
            data["keyframe_s3"] = f"s3://{config.s3_bucket}/{keyframe_s3}"
            uploaded_keyframes += 1
            if os.path.exists(keyframe_local):
                os.remove(keyframe_local)

        del data["confidences"]
        sqs_client.send_message(
            QueueUrl=config.output_sqs_url,
            MessageBody=json.dumps(data),
        )

    processing_time = time.time() - start_time_profile
    logger.info(
        "[%s] Processed %s in %.2fs. Detections=%s output_tracks=%s",
        source["source_id"],
        source["filename"],
        processing_time,
        total_detections,
        valid_tracks_count,
    )
    if source["source_type"] == "video":
        if pipeline_store and source["video_id"]:
            pipeline_store.update_video_diagnostics(
                source["video_id"],
                {
                    "frame_processor": {
                        "completed_at": datetime.utcnow().isoformat(),
                        "processing_seconds": round(processing_time, 2),
                        "sampled_frames": sampled_frames,
                        "detections": total_detections,
                        "tracks_seen": len(tracks_history),
                        "output_tracks": valid_tracks_count,
                        "keyframes_uploaded": uploaded_keyframes,
                    }
                },
            )
        update_video_status(source["video_id"], "completed")


def main() -> None:
    logger.info("Starting Frame Processor Service")

    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=config.input_sqs_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
            )

            messages = response.get("Messages", [])
            for message in messages:
                receipt_handle = message["ReceiptHandle"]
                body = json.loads(message["Body"])

                try:
                    process_chunk(body)
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle,
                    )
                except Exception as exc:
                    if is_uploaded_video_message(body):
                        update_video_status(
                            body.get("video_id"),
                            "failed",
                            error_message=str(exc),
                        )
                    logger.error("Error processing message: %s", exc, exc_info=True)

        except KeyboardInterrupt:
            logger.info("Shutting down gracefully...")
            break
        except Exception as exc:
            logger.error("SQS Receive error: %s", exc, exc_info=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
