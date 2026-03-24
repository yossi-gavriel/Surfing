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
from shared.utils.worker_safety import (
    GracefulShutdown,
    WorkerLeaseGuard,
    WorkerRuntimeStats,
    get_receive_count,
    send_to_dlq,
    worker_instance_id,
)

logger = get_logger("frame-processor")
WORKER_TYPE = "frame-processor"
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


def _record_worker_metric(name: str, value: int = 1) -> None:
    if not pipeline_store:
        return
    try:
        pipeline_store.increment_metric(f"worker.{WORKER_TYPE}.{name}", value)
    except Exception as exc:
        logger.warning("Failed to record worker metric %s: %s", name, exc)


def _log_worker_stats(stats: WorkerRuntimeStats) -> None:
    logger.info("Worker metrics snapshot: %s", stats.snapshot())


def _frame_job_key(msg_body: dict[str, Any]) -> str:
    if msg_body.get("idempotency_key"):
        return str(msg_body["idempotency_key"])
    video_id = msg_body.get("video_id") or os.path.splitext(
        msg_body.get("file_name") or os.path.basename(msg_body.get("s3_path", ""))
    )[0]
    timestamp = msg_body.get("timestamp") or msg_body.get("chunk_start") or "unknown"
    return f"frame:{video_id}:{timestamp}"


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


def has_video_record(video_id: str | None) -> bool:
    if not pipeline_store or not video_id:
        return False
    try:
        return pipeline_store.get_video(video_id) is not None
    except Exception as exc:
        logger.warning("Failed to lookup video %s: %s", video_id, exc)
        return False


def source_context(msg_body: dict[str, Any]) -> dict[str, Any]:
    source_type = "video" if is_uploaded_video_message(msg_body) else "camera"
    s3_path = msg_body["s3_path"]
    filename = msg_body.get("file_name") or os.path.basename(s3_path)
    inferred_video_id = os.path.splitext(filename)[0]
    video_id = msg_body.get("video_id") or inferred_video_id
    camera_id = msg_body.get("camera_id")
    pool_id = msg_body.get("pool_id")
    source_id = camera_id or video_id
    storage_key = camera_id or f"video-{video_id}"
    chunk_start_iso = msg_body.get("chunk_start") or msg_body.get("timestamp") or datetime.utcnow().isoformat()

    return {
        "source_type": source_type,
        "source_id": source_id,
        "storage_key": storage_key,
        "camera_id": camera_id,
        "pool_id": pool_id,
        "video_id": video_id,
        "s3_path": s3_path,
        "filename": filename,
        "chunk_start_iso": chunk_start_iso,
    }


def process_chunk(msg_body: dict[str, Any]) -> None:
    start_time_profile = time.time()
    started_at = datetime.utcnow().isoformat()
    source = source_context(msg_body)
    track_video_record = bool(source["video_id"])
    record_exists = has_video_record(source["video_id"]) if track_video_record else False
    if track_video_record and not record_exists:
        logger.warning(
            "[%s] Video record not found at frame start. Continuing with best-effort diagnostics writes.",
            source["video_id"],
        )
    if track_video_record:
        update_video_status(source["video_id"], "processing")
        queue_delay_seconds = None
        try:
            queued_at = datetime.fromisoformat(source["chunk_start_iso"].replace("Z", "+00:00"))
            queue_delay_seconds = round(
                max((datetime.utcnow() - queued_at.replace(tzinfo=None)).total_seconds(), 0.0),
                3,
            )
        except ValueError:
            queue_delay_seconds = None
        if pipeline_store and source["video_id"]:
            pipeline_store.update_video_diagnostics(
                source["video_id"],
                {
                    "frame_processor": {
                        "started_at": started_at,
                        "source_video": source["s3_path"],
                        "queued_at": msg_body.get("timestamp"),
                        "queue_delay_seconds": queue_delay_seconds,
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
                    "pool_id": source["pool_id"],
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
                    "debug_frames": [],
                }

            tracks_history[tid]["bboxes"].append(bbox)
            tracks_history[tid]["frames"].append(frame_idx)
            tracks_history[tid]["frame_timestamps"].append(current_time_iso)
            tracks_history[tid]["confidences"].append(conf)
            tracks_history[tid]["end_time"] = current_time_iso

            if track_video_record:
                bx1, by1, bx2, by2 = [int(value) for value in bbox]
                bx1, by1 = max(0, bx1), max(0, by1)
                bx2, by2 = min(frame.shape[1], bx2), min(frame.shape[0], by2)
                if bx2 > bx1 and by2 > by1:
                    crop = frame[by1:by2, bx1:bx2].copy()
                    debug_frame_name = f"{tid}_{frame_idx:06d}.jpg"
                    debug_frame_local = os.path.join(keyframe_dir, f"debug_{debug_frame_name}")
                    cv2.imwrite(debug_frame_local, crop)
                    debug_frame_s3_key = f"debug-frames/{source['storage_key']}/{debug_frame_name}"
                    s3_client.upload_file(debug_frame_local, config.s3_bucket, debug_frame_s3_key)
                    if os.path.exists(debug_frame_local):
                        os.remove(debug_frame_local)

                    debug_frame_s3 = f"s3://{config.s3_bucket}/{debug_frame_s3_key}"
                    debug_frame_record = {
                        "frame_index": frame_idx,
                        "frame_timestamp": current_time_iso,
                        "image_s3": debug_frame_s3,
                        "bbox": [float(value) for value in bbox],
                    }
                    tracks_history[tid]["debug_frames"].append(debug_frame_record)

                    if pipeline_store and source["video_id"]:
                        pipeline_store.upsert_video_debug_frame(
                            video_id=source["video_id"],
                            track_id=str(tid),
                            frame_index=frame_idx,
                            frame_timestamp=current_time_iso,
                            image_s3=debug_frame_s3,
                            bbox=[float(value) for value in bbox],
                        )

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
        data["idempotency_key"] = (
            f"embedding:{data.get('video_id') or data.get('source_video_id')}:{data['track_id']}:{data['start_time']}"
        )
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
    if track_video_record:
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
        if valid_tracks_count == 0:
            update_video_status(source["video_id"], "completed")


def main() -> None:
    logger.info("Starting Frame Processor Service")
    if not pipeline_store:
        logger.error("Pipeline store is unavailable; frame worker will not consume messages")
        return

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
                logger.warning("Frame worker lease lost; waiting before retrying leadership")
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
                    logger.error("Invalid frame message JSON: %s", exc)
                    stats.record_failure()
                    _record_worker_metric("failures")
                    send_to_dlq(
                        sqs_client=sqs_client,
                        dlq_url=None if not hasattr(config, "dlq_sqs_url") else config.dlq_sqs_url,
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

                job_key = _frame_job_key(body)
                if not pipeline_store.try_start_job(
                    job_type="frame_process",
                    job_key=job_key,
                    job_id=message.get("MessageId"),
                    payload=body,
                ):
                    logger.info("Skipping duplicate frame job job_key=%s", job_key)
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle,
                    )
                    _record_worker_metric("duplicates")
                    continue

                try:
                    process_chunk(body)
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
                    video_id = body.get("video_id") or os.path.splitext(
                        body.get("file_name") or os.path.basename(body.get("s3_path", ""))
                    )[0]
                    if has_video_record(video_id):
                        update_video_status(
                            video_id,
                            "failed",
                            error_message=str(exc),
                        )
                    logger.error("Error processing message: %s", exc, exc_info=True)
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
                            "Dead-lettering frame job job_key=%s receive_count=%s sent_to_dlq=%s",
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
                            "Frame job will be retried job_key=%s receive_count=%s",
                            job_key,
                            receive_count,
                        )

        except Exception as exc:
            logger.error("SQS Receive error: %s", exc, exc_info=True)
            shutdown.wait(5)
    try:
        lease_guard.release()
    except Exception as exc:
        logger.warning("Failed to release worker lease: %s", exc)
    _log_worker_stats(stats)


if __name__ == "__main__":
    main()
