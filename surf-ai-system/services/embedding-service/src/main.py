import os
import json
import time
import boto3
import sys
import cv2
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

from src.config import config
from src.face_detector import FaceDetector
from src.embedder import FaceEmbedder
from src.aggregator import EmbeddingAggregator
from shared.utils.face_preprocessing import preprocess_face, summarize_face_tensor
from shared.utils.logger import get_logger
from shared.utils.pipeline_store import PipelineStore

logger = get_logger("embedding-service")
sqs_client = boto3.client('sqs', region_name=config.aws_region)
s3_client = boto3.client('s3', region_name=config.aws_region)
pipeline_store = PipelineStore(os.environ.get("SQLITE_DB_PATH", "/app/data/surf_ai.db"))

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


def update_video_embedding_diagnostics(
    video_id: str | None,
    *,
    tracks_received_increment: int = 0,
    tracks_with_embeddings_increment: int = 0,
    tracks_without_faces_increment: int = 0,
    tracks_below_matching_threshold_increment: int = 0,
    valid_faces_detected_increment: int = 0,
    last_track_id: str | None = None,
    last_confidence: float | None = None,
):
    if not video_id:
        return

    existing = pipeline_store.get_video(video_id)
    if not existing:
        return

    embedding_data = (existing.get("diagnostics") or {}).get("embedding_service") or {}
    patch = {
        "embedding_service": {
            "tracks_received": int(embedding_data.get("tracks_received", 0)) + tracks_received_increment,
            "tracks_with_embeddings": int(embedding_data.get("tracks_with_embeddings", 0)) + tracks_with_embeddings_increment,
            "tracks_without_faces": int(embedding_data.get("tracks_without_faces", 0)) + tracks_without_faces_increment,
            "tracks_below_matching_threshold": int(embedding_data.get("tracks_below_matching_threshold", 0)) + tracks_below_matching_threshold_increment,
            "valid_faces_detected": int(embedding_data.get("valid_faces_detected", 0)) + valid_faces_detected_increment,
            "last_track_id": last_track_id or embedding_data.get("last_track_id"),
            "last_confidence": last_confidence if last_confidence is not None else embedding_data.get("last_confidence"),
            "updated_at": datetime.utcnow().isoformat(),
        }
    }
    pipeline_store.update_video_diagnostics(video_id, patch)

def process_track(msg_body: dict, detector: FaceDetector, embedder: FaceEmbedder, aggregator: EmbeddingAggregator):
    track_id = msg_body.get("track_id")
    camera_id = msg_body.get("camera_id")
    video_id = msg_body.get("video_id") or msg_body.get("source_video_id")
    update_video_embedding_diagnostics(
        video_id,
        tracks_received_increment=1,
        last_track_id=track_id,
    )
    
    keyframes = msg_body.get("keyframes", [])
    if "keyframe_s3" in msg_body and msg_body["keyframe_s3"]:
        keyframes.append(msg_body["keyframe_s3"])
        
    keyframes = list(set(keyframes))
    frames_received = len(keyframes)
    
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
                    processed_face = preprocess_face(
                        img,
                        bbox=best_face_data["face"].bbox,
                        kps=getattr(best_face_data["face"], "kps", None),
                    )
                    print(
                        {
                            "stage": "embedding_input",
                            **summarize_face_tensor(processed_face),
                        }
                    )
                    
                    emb = embedder.extract_embedding(best_face_data["face"])
                    
                    faces_data.append({
                        "embedding": emb,
                        "quality_score": best_face_data["quality_score"],
                        "det_score": best_face_data["det_score"]
                    })
                    
            if os.path.exists(local_path):
                os.remove(local_path)

    embeddings_created = len(faces_data)
    print(
        {
            "track_id": track_id,
            "frames_received": frames_received,
            "embeddings_created": embeddings_created,
        }
    )

    agg_emb, final_conf, num_faces, avg_quality, consistency = aggregator.aggregate(faces_data)
    
    if agg_emb is None:
        logger.warning(f"[{track_id}] Aggregate explicitly canceled mapped to 0 logical faces across keyframes.")
        update_video_embedding_diagnostics(
            video_id,
            tracks_without_faces_increment=1,
            last_track_id=track_id,
        )
        return

    video_embedding_record = None
    if video_id:
        video_embedding_record = pipeline_store.upsert_video_embedding(
            video_id=video_id,
            track_id=str(track_id),
            camera_id=camera_id,
            embedding=agg_emb,
            frames_received=frames_received,
            embeddings_created=embeddings_created,
            confidence=float(final_conf),
            consistency=float(consistency),
            keyframe_s3=msg_body.get("keyframe_s3"),
            start_time=msg_body.get("start_time"),
            end_time=msg_body.get("end_time"),
        )

    below_threshold = 1 if num_faces < config.matching_min_track_embeddings else 0
    update_video_embedding_diagnostics(
        video_id,
        tracks_with_embeddings_increment=1,
        tracks_below_matching_threshold_increment=below_threshold,
        valid_faces_detected_increment=num_faces,
        last_track_id=track_id,
        last_confidence=float(final_conf),
    )
        
    output_data = {
        "track_id": track_id,
        "camera_id": camera_id,
        "video_id": msg_body.get("video_id"),
        "source_video_id": msg_body.get("source_video_id"),
        "source_video_s3": msg_body.get("source_video_s3"),
        "keyframe_s3": msg_body.get("keyframe_s3"),
        "start_time": msg_body.get("start_time"),
        "end_time": msg_body.get("end_time"),
        "video_embedding_id": None if video_embedding_record is None else video_embedding_record["video_embedding_id"],
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
