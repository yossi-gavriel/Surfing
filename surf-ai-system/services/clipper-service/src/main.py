import os
import json
import time
import boto3
import sys
from datetime import datetime, timezone, timedelta

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from src.config import config
from src.clipper import VideoClipper
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

logger = get_logger("clipper-service")
WORKER_TYPE = "clipper-service"
sqs_client = boto3.client('sqs', region_name=config.aws_region)
s3_client = boto3.client('s3', region_name=config.aws_region)
pipeline_store = PipelineStore(os.environ.get("SQLITE_DB_PATH", "/app/data/surf_ai.db"))


def _record_worker_metric(name: str, value: int = 1) -> None:
    pipeline_store.increment_metric(f"worker.{WORKER_TYPE}.{name}", value)


def _clipper_job_key(msg_body: dict) -> str:
    if msg_body.get("idempotency_key"):
        return str(msg_body["idempotency_key"])
    return f"clipper:{msg_body.get('track_id')}:{msg_body.get('start_time') or msg_body.get('timestamp') or 'root'}"

def download_video(s3_key: str, local_path: str) -> bool:
    try:
        s3_client.download_file(config.s3_bucket, s3_key, local_path)
        return True
    except Exception as e:
        logger.error(f"Retrieval sequence broke structurally explicitly: {e}")
        return False

def download_s3_path(s3_path: str, local_path: str) -> bool:
    try:
        bucket = s3_path.split('//', 1)[1].split('/', 1)[0]
        key = s3_path.split(bucket + '/', 1)[1]
        s3_client.download_file(bucket, key, local_path)
        return True
    except Exception as e:
        logger.error(f"Direct source video download failed: {e}")
        return False

def find_s3_chunk(camera_id, start_dt, bucket):
    for offset_hours in [0, 1]:
        dt_check = start_dt - timedelta(hours=offset_hours)
        prefix_dir = f"raw/{camera_id}/{dt_check.strftime('%Y/%m/%d/%H')}/"
        try:
            response = s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix_dir)
            if 'Contents' not in response:
                continue
                
            chunks = []
            for obj in response['Contents']:
                key = obj['Key']
                if not key.endswith('.ts'): continue
                
                filename = key.split('/')[-1].replace('.ts', '')
                parts = filename.split('_')
                try:
                    time_str = parts[-1]
                    date_str = parts[-2]
                    chunk_dt = datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
                    chunk_dt = chunk_dt.replace(tzinfo=timezone.utc)
                    chunks.append((chunk_dt, key))
                except Exception:
                    continue
                    
            chunks.sort(key=lambda x: x[0])
            
            valid_chunk = None
            chunk_time = None
            for c_dt, key in chunks:
                if c_dt <= start_dt:
                    valid_chunk = key
                    chunk_time = c_dt
                else:
                    break
                    
            if valid_chunk:
                return valid_chunk, chunk_time
        except Exception as e:
            logger.error(f"Logical listing maps rejected dynamically natively: {e}")
            
    return None, None

def process_clip(msg_body: dict, clipper: VideoClipper):
    track_id = msg_body.get("track_id")
    camera_id = msg_body.get("camera_id")
    start_time_iso = msg_body.get("start_time")
    end_time_iso = msg_body.get("end_time")
    source_video_s3 = msg_body.get("source_video_s3")
    
    if not track_id or not camera_id or not start_time_iso or not end_time_iso:
        raise ValueError(f"Envelope bounds fundamentally compromised completely avoiding operation arrays constraints: {msg_body}")

    try:
        start_dt = datetime.fromisoformat(str(start_time_iso).replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(str(end_time_iso).replace('Z', '+00:00'))
    except Exception as e:
        logger.error(f"[{track_id}] Timestamp validation structure rigorously failed ISO specifications exactly: {e}")
        raise ValueError(f"invalid clip timestamps for track_id={track_id}") from e

    local_input = f"/tmp/{camera_id or 'video'}_{track_id}_input.ts"
    local_output = f"/tmp/{camera_id}_{track_id}_clip.mp4"
    
    try:
        if camera_id:
            s3_key, chunk_dt = find_s3_chunk(camera_id, start_dt, config.s3_bucket)
            if not s3_key:
                logger.error(f"[{track_id}] Exact topological physical source file mapped transparently empty logically over AWS architectures.")
                raise Exception("Root video dependencies organically absent rigorously preventing truncations safely natively.")

            offset_start = max(0.0, (start_dt - chunk_dt).total_seconds())
            offset_end = (end_dt - chunk_dt).total_seconds()
            if not download_video(s3_key, local_input):
                raise Exception("Base stream mapping physically threw extraction warnings definitively.")
        else:
            if not source_video_s3:
                raise Exception("Source video is required when camera_id is unavailable.")
            offset_start = 0.0
            offset_end = max(3.0, min(15.0, (end_dt - start_dt).total_seconds() + 2.0))
            local_input = f"/tmp/video_{track_id}_input.mp4"
            local_output = f"/tmp/video_{track_id}_clip.mp4"
            if not download_s3_path(source_video_s3, local_input):
                raise Exception("Failed to download direct source video.")
            
        if not clipper.clip_video(local_input, local_output, offset_start, offset_end):
            raise Exception("FFMPEG execution pipeline mapping securely explicitly trapped boundaries securely.")
         # 3. Upload logically
        s3_dest_key = f"rides/{track_id}.mp4"
        s3_client.upload_file(local_output, config.s3_bucket, s3_dest_key)
        logger.info(f"[{track_id}] Output mapping successfully bridged native bounds logically physically securely directly straight mapping: s3://{config.s3_bucket}/{s3_dest_key}")
        
        # 4. Generate & Upload Thumbnail gracefully
        local_thumb = f"/tmp/{camera_id}_{track_id}_thumb.jpg"
        import subprocess
        thumb_cmd = ["ffmpeg", "-y", "-i", local_output, "-ss", "00:00:00.500", "-vframes", "1", local_thumb]
        subprocess.run(thumb_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(local_thumb):
            s3_thumb_key = f"thumbnails/{track_id}.jpg"
            s3_client.upload_file(local_thumb, config.s3_bucket, s3_thumb_key)
            os.remove(local_thumb)
            logger.info(f"[{track_id}] Thumbnail captured and synced seamlessly to s3://{config.s3_bucket}/{s3_thumb_key}")
            
        # 5. Generate & Upload Fast Preview Clip seamlessly
        local_preview = f"/tmp/{camera_id}_{track_id}_preview.mp4"
        prev_cmd = ["ffmpeg", "-y", "-i", local_output, "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", local_preview]
        subprocess.run(prev_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if os.path.exists(local_preview):
            s3_prev_key = f"previews/{track_id}.mp4"
            s3_client.upload_file(local_preview, config.s3_bucket, s3_prev_key)
            os.remove(local_preview)
            logger.info(f"[{track_id}] Preview 3s snippet securely encoded and synced globally.")
        
    finally:
        if os.path.exists(local_input): os.remove(local_input)
        if os.path.exists(local_output): os.remove(local_output)

def main():
    logger.info("Initializing Topologically Driven Clipper Architecture Daemon Structurally")
    clipper = VideoClipper()
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
                logger.warning("Clipper worker lease lost; waiting before retrying leadership")
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
            
            messages = response.get('Messages', [])
            for message in messages:
                if shutdown.should_stop():
                    break
                receipt_handle = message['ReceiptHandle']
                receive_count = get_receive_count(message)
                
                try:
                    body = json.loads(message['Body'])
                except json.JSONDecodeError as exc:
                    logger.error("Invalid clipper message JSON: %s", exc)
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
                        ReceiptHandle=receipt_handle
                    )
                    continue

                job_key = _clipper_job_key(body)
                if not pipeline_store.try_start_job(
                    job_type="clipper_job",
                    job_key=job_key,
                    job_id=message.get("MessageId"),
                    payload=body,
                ):
                    logger.info("Skipping duplicate clipper job job_key=%s", job_key)
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle
                    )
                    _record_worker_metric("duplicates")
                    continue

                try:
                    process_clip(body, clipper)
                    pipeline_store.finish_job(job_key=job_key, status="completed")
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle
                    )
                    stats.record_processed()
                    _record_worker_metric("messages_processed")
                    if stats.processed % config.metrics_log_interval == 0:
                        logger.info("Clipper worker metrics snapshot: %s", stats.snapshot())
                except Exception as e:
                    pipeline_store.finish_job(
                        job_key=job_key,
                        status="failed",
                        error_message=str(e),
                    )
                    logger.error(f"Topological queue operations fundamentally aborted maintaining context dynamically for safe retries logically: {e}")
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
                            error_message=str(e),
                        )
                        if sent_to_dlq:
                            stats.record_dead_letter()
                            _record_worker_metric("dead_lettered")
                        sqs_client.delete_message(
                            QueueUrl=config.input_sqs_url,
                            ReceiptHandle=receipt_handle
                        )
                    else:
                        stats.record_retry()
                        _record_worker_metric("retries")
                    
        except Exception as e:
            logger.error(f"Network array polling rigorously errored mapping seamlessly: {e}")
            shutdown.wait(5)
    try:
        lease_guard.release()
    except Exception as exc:
        logger.warning("Failed to release clipper lease: %s", exc)
    logger.info("Clipper worker metrics snapshot: %s", stats.snapshot())

if __name__ == "__main__":
    main()
