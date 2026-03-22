import os
import time
import json
import threading
from datetime import datetime, timedelta
import boto3
import sys

# Allow importing from 'shared'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from src.config import config
from src.ffmpeg_runner import FFmpegRunner
from shared.utils.logger import get_logger
from shared.utils.s3_client import S3Client

logger = get_logger("ingestion-service")

sqs_client = boto3.client('sqs', region_name=config.aws_region)
s3_client = S3Client(region_name=config.aws_region)

def parse_chunk_times(filename: str, duration: int) -> tuple:
    """
    Parses cam1_20260321_200000.ts to start and end times in ISO format.
    Returns (chunk_start_iso, chunk_end_iso)
    """
    base = filename.rsplit('.', 1)[0]
    parts = base.split('_')
    
    if len(parts) >= 3:
        # Expected parts: cam1, 20260321, 200000
        date_str = parts[-2]
        time_str = parts[-1]
        try:
            start_dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
            end_dt = start_dt + timedelta(seconds=duration)
            return start_dt.isoformat(), end_dt.isoformat()
        except ValueError:
            pass
            
    # Fallback if unparseable
    now = datetime.utcnow()
    return now.isoformat(), (now + timedelta(seconds=duration)).isoformat()

def send_sqs_with_retry(msg_body: dict, max_retries: int = 3) -> bool:
    attempt = 0
    backoff = 1.0
    while attempt < max_retries:
        try:
            sqs_client.send_message(
                QueueUrl=config.sqs_queue_url,
                MessageBody=json.dumps(msg_body)
            )
            return True
        except Exception as e:
            logger.error(f"Error sending SQS message: {e}")
            attempt += 1
            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2.0
    return False

def upload_and_notify(camera_id: str, file_path: str) -> bool:
    logger.info(f"[{camera_id}] Processing stabilized segment: {file_path}")
    
    filename = os.path.basename(file_path)
    start_iso, end_iso = parse_chunk_times(filename, config.chunk_duration)
    
    try:
        dt = datetime.fromisoformat(start_iso)
        year, month, day, hour = dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d"), dt.strftime("%H")
    except ValueError:
        now = datetime.utcnow()
        year, month, day, hour = now.strftime("%Y"), now.strftime("%m"), now.strftime("%d"), now.strftime("%H")

    s3_key = f"raw/{camera_id}/{year}/{month}/{day}/{hour}/{filename}"
    
    success = s3_client.upload_file(file_path, config.s3_bucket, s3_key)
    
    if success:
        msg_body = {
            "camera_id": camera_id,
            "s3_path": f"s3://{config.s3_bucket}/{s3_key}",
            "timestamp": datetime.utcnow().isoformat(),
            "file_name": filename,
            "chunk_start": start_iso,
            "chunk_end": end_iso
        }
        
        sqs_success = send_sqs_with_retry(msg_body)
        if sqs_success:
            logger.info(f"[{camera_id}] Sent SQS message for {filename}")
            try:
                os.remove(file_path)
                logger.debug(f"[{camera_id}] Cleaned up local file {file_path}")
            except Exception as e:
                logger.error(f"[{camera_id}] Failed to delete file {file_path}: {e}")
            return True
        else:
            logger.error(f"[{camera_id}] SQS send failed after retries for {filename}.")
            # Return false to keep in unprocessed set
            return False
    else:
        logger.error(f"[{camera_id}] Failed to upload {file_path}. Keeping locally for retry.")
        return False

def poll_directory(camera_id: str, directory: str, stop_event: threading.Event):
    logger.info(f"[{camera_id}] Started polling directory: {directory}")
    processed_files = set()
    file_sizes = {}
    
    while not stop_event.is_set():
        try:
            if os.path.exists(directory):
                files = [os.path.join(directory, f) for f in os.listdir(directory) if f.endswith('.ts')]
                
                current_sizes = {}
                for f in files:
                    try:
                        current_sizes[f] = os.path.getsize(f)
                    except OSError:
                        pass
                
                ready_files = []
                # Check for file size stabilization
                for f, size in current_sizes.items():
                    if f in file_sizes and file_sizes[f] == size:
                        if size > 0:
                            ready_files.append(f)
                    
                file_sizes = current_sizes
                
                # Exclude the newest file additionally as it may still be active
                if files:
                    files.sort(key=os.path.getmtime)
                    newest = files[-1]
                    if newest in ready_files:
                        ready_files.remove(newest)
                
                for f in ready_files:
                    if f not in processed_files:
                        processed_files.add(f)
                        success = upload_and_notify(camera_id, f)
                        if not success:
                            # Retry on failure, effectively avoiding multiple parallel executions of the same file
                            processed_files.remove(f)
                            
                # Cleanup internal state mappings
                for f in list(processed_files):
                    if not os.path.exists(f):
                        processed_files.remove(f)
                
                for f in list(file_sizes.keys()):
                    if not os.path.exists(f):
                        del file_sizes[f]

        except Exception as e:
            logger.error(f"[{camera_id}] Error while polling directory: {e}")
            
        time.sleep(3)

def run_camera(camera_config: dict, stop_event: threading.Event, runners: list):
    camera_id = camera_config["camera_id"]
    rtsp_url = camera_config["rtsp_url"]
    
    # Requirement: Add per-camera temp directories
    output_dir = f"/tmp/{camera_id}/"
    os.makedirs(output_dir, exist_ok=True)
    
    poller_thread = threading.Thread(target=poll_directory, args=(camera_id, output_dir, stop_event))
    poller_thread.daemon = True
    poller_thread.start()
    
    runner = FFmpegRunner(
        camera_id=camera_id,
        rtsp_url=rtsp_url,
        chunk_duration=config.chunk_duration,
        output_dir=output_dir 
    )
    runners.append(runner)
    
    try:
        runner.start()
    except Exception as e:
        logger.error(f"[{camera_id}] Runner encountered an error: {e}")
    finally:
        runner.stop()

def main():
    logger.info("Starting Ingestion Service")
    cameras = config.cameras
    if not cameras:
        logger.error("No cameras found in config! Exiting.")
        return

    logger.info(f"Found {len(cameras)} cameras. Starting workers...")
    
    stop_event = threading.Event()
    threads = []
    runners = []
    
    try:
        for cam in cameras:
            t = threading.Thread(target=run_camera, args=(cam, stop_event, runners))
            t.daemon = True
            t.start()
            threads.append(t)
            
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down gracefully...")
        stop_event.set()
        for runner in runners:
            runner.stop()
        for t in threads:
            t.join(timeout=10)
        logger.info("Shutdown complete.")

if __name__ == "__main__":
    main()
