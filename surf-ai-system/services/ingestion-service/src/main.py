import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import boto3

from shared.utils.logger import get_logger
from shared.utils.pipeline_store import PipelineStore
from shared.utils.s3_client import S3Client
from src.config import config
from src.ffmpeg_runner import FFmpegRunner

logger = get_logger("ingestion-service")

sqs_client = boto3.client("sqs", region_name=config.aws_region)
s3_client = S3Client(region_name=config.aws_region)
pipeline_store = PipelineStore(config.sqlite_db_path)


def parse_chunk_times(filename: str, duration: int) -> tuple[str, str]:
    base = filename.rsplit(".", 1)[0]
    parts = base.split("_")

    if len(parts) >= 3:
        date_str = parts[-2]
        time_str = parts[-1]
        try:
            start_dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
            end_dt = start_dt + timedelta(seconds=duration)
            return start_dt.isoformat(), end_dt.isoformat()
        except ValueError:
            pass

    now = datetime.utcnow()
    return now.isoformat(), (now + timedelta(seconds=duration)).isoformat()


def send_sqs_with_retry(msg_body: dict[str, Any], max_retries: int = 3) -> bool:
    attempt = 0
    backoff = 1.0
    while attempt < max_retries:
        try:
            sqs_client.send_message(
                QueueUrl=config.sqs_queue_url,
                MessageBody=json.dumps(msg_body),
            )
            return True
        except Exception as exc:
            attempt += 1
            logger.error("Error sending SQS message on attempt %s: %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2.0
    return False


def upload_and_notify(camera_config: dict[str, Any], file_path: str) -> bool:
    camera_id = camera_config["camera_id"]
    filename = os.path.basename(file_path)
    logger.info("[%s] Processing stabilized segment %s", camera_id, filename)

    start_iso, end_iso = parse_chunk_times(filename, config.chunk_duration)
    try:
        dt = datetime.fromisoformat(start_iso)
    except ValueError:
        dt = datetime.utcnow()

    s3_key = (
        f"raw/{camera_id}/"
        f"{dt.strftime('%Y')}/{dt.strftime('%m')}/{dt.strftime('%d')}/{dt.strftime('%H')}/"
        f"{filename}"
    )
    s3_path = f"s3://{config.s3_bucket}/{s3_key}"

    if not s3_client.upload_file(file_path, config.s3_bucket, s3_key):
        logger.error("[%s] Upload failed for %s", camera_id, filename)
        return False

    message = {
        "type": "camera",
        "camera_id": camera_id,
        "pool_id": camera_config.get("pool_id"),
        "video_id": os.path.splitext(filename)[0],
        "s3_path": s3_path,
        "timestamp": datetime.utcnow().isoformat(),
        "file_name": filename,
        "chunk_start": start_iso,
        "chunk_end": end_iso,
    }

    if not send_sqs_with_retry(message):
        logger.error("[%s] Queue publish failed for %s", camera_id, filename)
        return False

    logger.info("[%s] Uploaded and queued %s", camera_id, filename)
    try:
        os.remove(file_path)
    except OSError as exc:
        logger.warning("[%s] Failed to delete %s: %s", camera_id, file_path, exc)
    return True


def poll_directory(camera_config: dict[str, Any], directory: str, stop_event: threading.Event) -> None:
    camera_id = camera_config["camera_id"]
    processed_files: set[str] = set()
    file_sizes: dict[str, int] = {}

    logger.info("[%s] Polling directory %s", camera_id, directory)
    while not stop_event.is_set():
        try:
            if os.path.exists(directory):
                files = [os.path.join(directory, item) for item in os.listdir(directory) if item.endswith(".ts")]
                current_sizes: dict[str, int] = {}
                for path in files:
                    try:
                        current_sizes[path] = os.path.getsize(path)
                    except OSError:
                        continue

                ready_files: list[str] = []
                for path, size in current_sizes.items():
                    if size > 0 and file_sizes.get(path) == size:
                        ready_files.append(path)

                file_sizes = current_sizes
                if files:
                    files.sort(key=os.path.getmtime)
                    newest = files[-1]
                    if newest in ready_files:
                        ready_files.remove(newest)

                for path in ready_files:
                    if path in processed_files:
                        continue
                    processed_files.add(path)
                    if not upload_and_notify(camera_config, path):
                        processed_files.discard(path)

                for path in list(processed_files):
                    if not os.path.exists(path):
                        processed_files.discard(path)

                for path in list(file_sizes.keys()):
                    if not os.path.exists(path):
                        del file_sizes[path]
        except Exception as exc:
            logger.error("[%s] Directory polling error: %s", camera_id, exc, exc_info=True)

        stop_event.wait(3)


def camera_signature(camera_config: dict[str, Any]) -> str:
    return "|".join(
        [
            str(camera_config.get("camera_id", "")),
            str(camera_config.get("name", "")),
            str(camera_config.get("url", "")),
            str(camera_config.get("rtsp_url", "")),
        ]
    )


def run_camera(camera_config: dict[str, Any], stop_event: threading.Event, runner: FFmpegRunner) -> None:
    camera_id = camera_config["camera_id"]
    output_dir = runner.output_dir
    os.makedirs(output_dir, exist_ok=True)

    poller_thread = threading.Thread(
        target=poll_directory,
        args=(camera_config, output_dir, stop_event),
        daemon=True,
    )
    poller_thread.start()

    try:
        runner.start()
    except Exception as exc:
        logger.error("[%s] Runner failed: %s", camera_id, exc, exc_info=True)
    finally:
        stop_event.set()
        runner.stop()
        poller_thread.join(timeout=5)


def start_camera_worker(camera_config: dict[str, Any]) -> dict[str, Any] | None:
    camera_id = camera_config.get("camera_id")
    stream_url = camera_config.get("url") or camera_config.get("rtsp_url")
    if not camera_id or not stream_url:
        logger.warning("Skipping invalid camera config: %s", camera_config)
        return None

    output_dir = f"/tmp/{camera_id}"
    stop_event = threading.Event()
    runner = FFmpegRunner(
        camera_id=camera_id,
        rtsp_url=stream_url,
        chunk_duration=config.chunk_duration,
        output_dir=output_dir,
    )
    thread = threading.Thread(
        target=run_camera,
        args=(camera_config, stop_event, runner),
        daemon=True,
    )
    thread.start()
    logger.info("[%s] Camera worker started", camera_id)
    return {
        "thread": thread,
        "stop_event": stop_event,
        "runner": runner,
        "signature": camera_signature(camera_config),
    }


def stop_camera_worker(camera_id: str, worker: dict[str, Any]) -> None:
    logger.info("[%s] Stopping camera worker", camera_id)
    worker["stop_event"].set()
    worker["runner"].stop()
    worker["thread"].join(timeout=10)


def load_camera_configs() -> list[dict[str, Any]]:
    cameras = pipeline_store.list_active_cameras()
    if cameras:
        return cameras

    fallback: list[dict[str, Any]] = []
    for index, camera in enumerate(config.cameras):
        stream_url = camera.get("url") or camera.get("rtsp_url")
        if not stream_url:
            continue
        fallback.append(
            {
                "camera_id": camera.get("camera_id") or f"camera-{index + 1}",
                "name": camera.get("name") or f"Camera {index + 1}",
                "url": stream_url,
                "active": True,
            }
        )
    return fallback


def main() -> None:
    logger.info("Starting Ingestion Service")
    workers: dict[str, dict[str, Any]] = {}

    try:
        while True:
            cameras = load_camera_configs()
            desired = {camera["camera_id"]: camera for camera in cameras}

            for camera_id in list(workers.keys()):
                if camera_id not in desired:
                    stop_camera_worker(camera_id, workers.pop(camera_id))

            for camera_id, camera_config in desired.items():
                signature = camera_signature(camera_config)
                worker = workers.get(camera_id)
                if worker and worker["signature"] == signature:
                    continue
                if worker:
                    stop_camera_worker(camera_id, workers.pop(camera_id))

                new_worker = start_camera_worker(camera_config)
                if new_worker:
                    workers[camera_id] = new_worker

            if not desired:
                logger.info("No active cameras configured. Waiting for camera registrations...")

            time.sleep(config.camera_poll_interval)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down ingestion workers")
    finally:
        for camera_id, worker in list(workers.items()):
            stop_camera_worker(camera_id, worker)
        logger.info("Ingestion shutdown complete")


if __name__ == "__main__":
    main()
