import os
import json
import time
import boto3
import sys
import cv2

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from src.config import config
from src.face_detector import FaceDetector
from src.embedder import FaceEmbedder
from src.aggregator import EmbeddingAggregator
from shared.utils.logger import get_logger

logger = get_logger("embedding-service")
sqs_client = boto3.client('sqs', region_name=config.aws_region)
s3_client = boto3.client('s3', region_name=config.aws_region)

def download_image(s3_path: str, local_path: str) -> bool:
    bucket = s3_path.split('//')[1].split('/')[0]
    key = s3_path.split(bucket + '/')[1]
    try:
        s3_client.download_file(bucket, key, local_path)
        return True
    except Exception as e:
        logger.error(f"Failed to download {s3_path}: {e}")
        return False

def compute_quality_score(det_score, face_size, blur_score):
    size_weight = min(face_size / 200.0, 1.0)
    blur_weight = min(blur_score / 500.0, 1.0)
    return float(det_score * size_weight * blur_weight)

def process_track(msg_body: dict, detector: FaceDetector, embedder: FaceEmbedder, aggregator: EmbeddingAggregator):
    track_id = msg_body.get("track_id")
    camera_id = msg_body.get("camera_id")
    
    keyframes = msg_body.get("keyframes", [])
    if "keyframe_s3" in msg_body and msg_body["keyframe_s3"]:
        keyframes.append(msg_body["keyframe_s3"])
        
    keyframes = list(set(keyframes))
    
    if not keyframes:
        logger.info(f"[{track_id}] No keyframes securely identified. Gracefully bypassing extraction arrays.")
        return
        
    faces_data = []
    frames_processed = 0
    
    for idx, s3_path in enumerate(keyframes):
        local_path = f"/tmp/{camera_id}_{track_id}_{idx}.jpg"
        if download_image(s3_path, local_path):
            img = cv2.imread(local_path)
            if img is not None:
                frames_processed += 1
                faces = detector.detect(img)
                
                valid_faces = []
                for face in faces:
                    x1, y1, x2, y2 = face.bbox
                    width, height = x2 - x1, y2 - y1
                    face_size = max(width, height)
                    
                    if face_size < config.min_face_size:
                        continue
                    if face.det_score < config.min_confidence:
                        continue
                        
                    # Explicit Pose validation check
                    if not detector.check_pose(face, config.max_yaw, config.max_pitch):
                        continue
                        
                    blur = detector.get_blur_score(img, face.bbox)
                    if blur < config.min_blur_score:
                        continue
                        
                    quality = compute_quality_score(face.det_score, face_size, blur)
                    
                    valid_faces.append({
                        "face": face,
                        "size": face_size,
                        "blur": blur,
                        "det_score": face.det_score,
                        "quality_score": quality
                    })
                    
                if valid_faces:
                    # Select robustly exclusively mapped logically via quality arrays
                    best_face_data = sorted(valid_faces, key=lambda x: x["quality_score"], reverse=True)[0]
                    
                    emb = embedder.extract_embedding(best_face_data["face"])
                    
                    faces_data.append({
                        "embedding": emb,
                        "quality_score": best_face_data["quality_score"],
                        "det_score": best_face_data["det_score"]
                    })
                    
            if os.path.exists(local_path):
                os.remove(local_path)
                
    agg_emb, final_conf, num_faces, avg_quality, consistency = aggregator.aggregate(faces_data)
    
    if agg_emb is None:
        logger.warning(f"[{track_id}] Aggregate explicitly canceled mapped to 0 logical faces across keyframes.")
        return
        
    output_data = {
        "track_id": track_id,
        "camera_id": camera_id,
        "face_embedding": agg_emb,
        "embedding_confidence": float(final_conf),
        "num_faces_detected": num_faces,
        "avg_quality": float(avg_quality),
        "consistency": float(consistency)
    }
    
    sqs_client.send_message(
        QueueUrl=config.output_sqs_url,
        MessageBody=json.dumps(output_data)
    )
    
    logger.info(f"[{track_id}] Output embedding. Frames evaluating: {frames_processed}, "
                f"Valid Faces captured: {num_faces}, Final Aggregated Confidence Vector: {final_conf:.2f}")

def main():
    logger.info("Starting Embedding Service Orchestration Worker")
    
    detector = FaceDetector(model_name=config.model_name, ctx_id=config.ctx_id)
    embedder = FaceEmbedder()
    aggregator = EmbeddingAggregator(
        max_similarity=config.max_similarity,
        min_samples=config.min_samples
    )
    
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
                    process_track(body, detector, embedder, aggregator)
                    sqs_client.delete_message(
                        QueueUrl=config.input_sqs_url,
                        ReceiptHandle=receipt_handle
                    )
                except Exception as e:
                    logger.error(f"Error processing message payload internally: {e}")
                    
        except KeyboardInterrupt:
            logger.info("Shutting down worker environment gracefully...")
            break
        except Exception as e:
            logger.error(f"SQS Interface integration error offset: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
