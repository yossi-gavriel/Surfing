import os
import json
import time
import boto3
import cv2
import redis
from datetime import datetime

from services.frame_processor.src.config import config
from services.frame_processor.src.detector import PersonDetector
from services.frame_processor.src.tracker import IoUTracker
from services.frame_processor.src.frame_loader import extract_frames
from services.frame_processor.src.zones import ZoneCalculator
from shared.utils.logger import get_logger

logger = get_logger("frame-processor")
sqs_client = boto3.client('sqs', region_name=config.aws_region)
s3_client = boto3.client('s3', region_name=config.aws_region)

try:
    redis_client = redis.Redis(host=config.redis_host, port=config.redis_port, db=0, decode_responses=True)
    redis_client.ping()
except Exception as e:
    logger.warning(f"Redis initialization failed ({e}). Proceeding without global cache.")
    redis_client = None

def download_video(s3_path: str, local_path: str):
    bucket = s3_path.split('//')[1].split('/')[0]
    key = s3_path.split(bucket + '/')[1]
    logger.info(f"Downloading {s3_path} to {local_path}")
    s3_client.download_file(bucket, key, local_path)

def process_chunk(msg_body: dict):
    start_time_profile = time.time()
    
    camera_id = msg_body["camera_id"]
    s3_path = msg_body["s3_path"]
    filename = msg_body.get("file_name", os.path.basename(s3_path))
    chunk_start_iso = msg_body.get("chunk_start", datetime.utcnow().isoformat())
    
    local_path = f"/tmp/{filename}"
    download_video(s3_path, local_path)
    
    keyframe_dir = f"/tmp/keyframes/{camera_id}"
    os.makedirs(keyframe_dir, exist_ok=True)
    
    detector = PersonDetector(
        model_name=config.model_name,
        min_confidence=config.min_confidence,
        inference_size=(config.inference_width, config.inference_height),
        min_bbox_area=config.min_bbox_area,
        max_aspect_ratio=config.max_aspect_ratio
    )
    
    dt_iso = chunk_start_iso.replace('Z', '+00:00')
    dt_chunk = datetime.fromisoformat(dt_iso)
    prefix_id = f"{camera_id}_{dt_chunk.strftime('%Y%m%d_%H%M%S')}"
    
    # Establish centralized tracking instances natively from Redis persistence
    tracker = IoUTracker(
        prefix_id=prefix_id,
        camera_id=camera_id,
        redis_client=redis_client,
        iou_threshold=0.3, 
        center_dist_threshold=config.center_dist_threshold,
        max_active=config.max_active_tracks,
        max_speed=config.max_velocity,
        conf_decay=config.conf_decay
    )
    
    tracks_history = {}
    total_detections = 0
    frame_width = None
    zone_calc = None
    
    for frame_idx, timestamp_sec, frame in extract_frames(local_path, config.frame_sample_rate):
        if frame_width is None:
            frame_width = frame.shape[1]
            zone_calc = ZoneCalculator(frame_width)
            
        bboxes_info = detector.detect(frame)
        total_detections += len(bboxes_info)
        
        tracked_objects = tracker.update(bboxes_info)
        current_time_iso = (dt_chunk + __import__('datetime').timedelta(seconds=timestamp_sec)).isoformat()
        
        if config.debug_mode:
            debug_frame = frame.copy()
        
        for tid, bbox, conf in tracked_objects:
            if config.debug_mode:
                x1, y1, x2, y2 = [int(v) for v in bbox]
                cv2.rectangle(debug_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(debug_frame, f"{tid} {conf:.2f}", (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            if tid not in tracks_history:
                tracks_history[tid] = {
                    "camera_id": camera_id,
                    "track_id": str(tid),
                    "bboxes": [],
                    "frames": [],
                    "frame_timestamps": [],
                    "confidences": [],
                    "start_time": current_time_iso,
                    "end_time": current_time_iso,
                    "best_conf": 0.0,
                    "best_frame_crop": None
                }
            
            tracks_history[tid]["bboxes"].append(bbox)
            tracks_history[tid]["frames"].append(frame_idx)
            tracks_history[tid]["frame_timestamps"].append(current_time_iso)
            tracks_history[tid]["confidences"].append(conf)
            tracks_history[tid]["end_time"] = current_time_iso
            
            # Keyframe Extraction logic natively locking frames with max accuracy
            if conf > tracks_history[tid]["best_conf"]:
                tracks_history[tid]["best_conf"] = conf
                bx1, by1, bx2, by2 = [int(v) for v in bbox]
                bx1, by1 = max(0, bx1), max(0, by1)
                bx2, by2 = min(frame.shape[1], bx2), min(frame.shape[0], by2)
                
                if bx2 > bx1 and by2 > by1:
                    tracks_history[tid]["best_frame_crop"] = frame[by1:by2, bx1:bx2].copy()

        if config.debug_mode:
            os.makedirs(config.debug_output_dir, exist_ok=True)
            cv2.imwrite(f"{config.debug_output_dir}/{camera_id}_{filename}_{frame_idx:04d}.jpg", debug_frame)

    os.remove(local_path)
    tracker.save_state()
    
    valid_tracks_count = 0
    for tid, data in tracks_history.items():
        if len(data["frames"]) >= config.min_track_length:
            
            data["num_detections"] = len(data["frames"])
            data["duration_frames"] = len(data["frames"])
            data["avg_confidence"] = sum(data["confidences"]) / float(data["duration_frames"])
            data["max_confidence"] = max(data["confidences"])
            
            # Combined mathematical track scoring optimization!
            duration_weight = min(data["duration_frames"] / 15.0, 1.0)
            track_score = (data["avg_confidence"] * 0.4 + data["max_confidence"] * 0.6) * duration_weight
            data["track_score"] = float(track_score)
            
            if track_score < config.min_track_score:
                continue

            valid_tracks_count += 1
            
            first_bbox = data["bboxes"][0]
            last_bbox = data["bboxes"][-1]
            data["entry_zone"] = zone_calc.get_zone(first_bbox)
            data["exit_zone"] = zone_calc.get_zone(last_bbox)
            
            # Embed Keyframe explicitly caching cleanly to s3 ecosystem
            crop_img = data.pop("best_frame_crop", None)
            data.pop("best_conf", None)
            
            if crop_img is not None:
                keyframe_name = f"{tid}_{filename}.jpg"
                keyframe_local = f"{keyframe_dir}/{keyframe_name}"
                cv2.imwrite(keyframe_local, crop_img)
                keyframe_s3 = f"keyframes/{camera_id}/{keyframe_name}"
                s3_client.upload_file(keyframe_local, config.s3_bucket, keyframe_s3)
                data["keyframe_s3"] = f"s3://{config.s3_bucket}/{keyframe_s3}"
                if os.path.exists(keyframe_local):
                    os.remove(keyframe_local)
            
            del data["confidences"]
            
            sqs_client.send_message(
                QueueUrl=config.output_sqs_url,
                MessageBody=json.dumps(data)
            )
            
    processing_time = time.time() - start_time_profile
    logger.info(f"[{camera_id}] Processed {filename} in {processing_time:.2f}s. "
                f"Detections: {total_detections}, Scored/Output Tracks: {valid_tracks_count}")

def main():
    logger.info("Starting Frame Processor Service")
    
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
                    process_chunk(body)
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle
                    )
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    
        except KeyboardInterrupt:
            logger.info("Shutting down gracefully...")
            break
        except Exception as e:
            logger.error(f"SQS Receive error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
