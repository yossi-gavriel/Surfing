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

logger = get_logger("clipper-service")
sqs_client = boto3.client('sqs', region_name=config.aws_region)
s3_client = boto3.client('s3', region_name=config.aws_region)

def download_video(s3_key: str, local_path: str) -> bool:
    try:
        s3_client.download_file(config.s3_bucket, s3_key, local_path)
        return True
    except Exception as e:
        logger.error(f"Retrieval sequence broke structurally explicitly: {e}")
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
    
    if not track_id or not camera_id or not start_time_iso or not end_time_iso:
        raise ValueError(f"Envelope bounds fundamentally compromised completely avoiding operation arrays constraints: {msg_body}")

    try:
        start_dt = datetime.fromisoformat(str(start_time_iso).replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(str(end_time_iso).replace('Z', '+00:00'))
    except Exception as e:
        logger.error(f"[{track_id}] Timestamp validation structure rigorously failed ISO specifications exactly: {e}")
        return

    s3_key, chunk_dt = find_s3_chunk(camera_id, start_dt, config.s3_bucket)
    if not s3_key:
        logger.error(f"[{track_id}] Exact topological physical source file mapped transparently empty logically over AWS architectures.")
        # Generates explicit failure throwing straight back enabling logical native AWS SQS visibility timeouts retrying structurally
        raise Exception("Root video dependencies organically absent rigorously preventing truncations safely natively.")

    offset_start = max(0.0, (start_dt - chunk_dt).total_seconds())
    offset_end = (end_dt - chunk_dt).total_seconds()
    
    local_input = f"/tmp/{camera_id}_{track_id}_input.ts"
    local_output = f"/tmp/{camera_id}_{track_id}_clip.mp4"
    
    try:
        if not download_video(s3_key, local_input):
            raise Exception("Base stream mapping physically threw extraction warnings definitively.")
            
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
    
    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=config.input_sqs_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20
            )
            
            messages = response.get('Messages', [])
            for message in messages:
                receipt_handle = message['ReceiptHandle']
                body = json.loads(message['Body'])
                
                try:
                    process_clip(body, clipper)
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle
                    )
                except Exception as e:
                    logger.error(f"Topological queue operations fundamentally aborted maintaining context dynamically for safe retries logically: {e}")
                    
        except KeyboardInterrupt:
            logger.info("Safely terminating extraction bindings inherently sequentially.")
            break
        except Exception as e:
            logger.error(f"Network array polling rigorously errored mapping seamlessly: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
