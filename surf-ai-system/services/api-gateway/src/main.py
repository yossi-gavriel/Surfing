from fastapi import FastAPI, UploadFile, File
import cv2
import numpy as np
import uuid
import boto3
import json
import asyncio
import os
from insightface.app import FaceAnalysis

from src.db import JsonDB

from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import time

app = FastAPI(title="Surf AI REST Infrastructure Gateway")

# Enable analytical frontend bindings naturally pushing natively across domains structurally natively
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def log_analytics(event_name, details):
    import json
    log_entry = {
        "event": event_name,
        "timestamp": datetime.utcnow().isoformat(),
        "details": details
    }
    print(json.dumps(log_entry))

db = JsonDB()
start_time = time.time()

@app.get("/health")
async def health_check():
    return {"status": "ok", "uptime_seconds": int(time.time() - start_time)}

@app.get("/metrics")
async def get_metrics():
    return {
        "status": "ok", 
        "uptime_seconds": int(time.time() - start_time), 
        "active_cached_urls": len(url_cache)
    }

# InsightFace loads model binaries formally handling extraction configurations inside endpoints inherently explicitly 
face_app = FaceAnalysis(name='buffalo_s', providers=['CPUExecutionProvider'])
face_app.prepare(ctx_id=-1, det_size=(640, 640))

s3_bucket = os.environ.get("S3_BUCKET", "surf-ai-bucket")
matches_queue_url = os.environ.get("MATCHING_OUTPUT_SQS_URL")
aws_region = os.environ.get("AWS_REGION", "us-east-1")

sqs_client = boto3.client('sqs', region_name=aws_region)
s3_client = boto3.client('s3', region_name=aws_region)

url_cache = {}
def get_cached_presigned_url(bucket, key, expires_in=3600):
    cache_key = f"{bucket}/{key}"
    now = time.time()
    
    if cache_key in url_cache:
        cached = url_cache[cache_key]
        if cached['expires_at'] > now + 300:
            return cached['url']
            
    try:
        url = s3_client.generate_presigned_url('get_object', Params={'Bucket': bucket, 'Key': key}, ExpiresIn=expires_in)
        if url:
            url_cache[cache_key] = {'url': url, 'expires_at': now + expires_in}
        return url
    except Exception as e:
        print(f"Presigned caching log routing failed bounds efficiently: {e}")
        return None

async def sqs_consumer():
    if not matches_queue_url: return
    while True:
        try:
            response = await asyncio.to_thread(
                sqs_client.receive_message,
                QueueUrl=matches_queue_url,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=10
            )
            messages = response.get('Messages', [])
            for msg in messages:
                body = json.loads(msg['Body'])
                user_id = body.get('user_id')
                track_id = body.get('track_id')
                score = body.get('score')
                conf = body.get('confidence')
                
                if user_id and track_id:
                    db.add_ride(user_id, track_id, score, conf, s3_bucket)
                    log_analytics("ride_generated", {
                        "user_id": user_id, 
                        "track_id": track_id, 
                        "score": score, 
                        "confidence": conf
                    })
                    
                await asyncio.to_thread(
                    sqs_client.delete_message,
                    QueueUrl=matches_queue_url,
                    ReceiptHandle=msg['ReceiptHandle']
                )
        except Exception as e:
            print(f"Async SQS Consumer error explicitly tracking boundary topologies natively: {e}")
        await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(sqs_consumer())

@app.post("/users")
async def register_user(file: UploadFile = File(...)):
    """
    Standardizes image payload converting inherently mapped sequences extracting raw topologies intelligently.
    """
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    if img is None:
        return {"error": "invalid_image", "message": "פורמט תמונה לא נתמך. נסה תמונה אחרת."}
        
    faces = face_app.get(img)
    if not faces:
        return {"error": "no_face_detected", "message": "לא זוהו פנים בתמונה. נסה תמונה ברורה יותר הממוקדת בפנים."}
        
    # Pick largest inherently 
    faces = sorted(faces, key=lambda f: (f.bbox[2]-f.bbox[0])*(f.bbox[3]-f.bbox[1]), reverse=True)
    best_face = faces[0]
    
    emb = best_face.embedding.tolist()
    user_id = str(uuid.uuid4())
    
    db.add_user(user_id, emb)
    log_analytics("user_upload", {"user_id": user_id})
    return {"message": "User instantiated robustly safely alongside analytical topologies", "user_id": user_id}

@app.get("/users/{user_id}/rides")
async def get_user_rides(user_id: str):
    rides = db.get_rides(user_id)
    if not rides:
        return {"user_id": user_id, "status": "processing", "rides": []}
        
    rides.sort(key=lambda x: x["track_id"], reverse=True)
    enriched = []
    for r in rides:
        tid = r["track_id"]
        v_url = get_cached_presigned_url(s3_bucket, f"rides/{tid}.mp4")
        t_url = get_cached_presigned_url(s3_bucket, f"thumbnails/{tid}.jpg")
        p_url = get_cached_presigned_url(s3_bucket, f"previews/{tid}.mp4")
        
        r_copy = dict(r)
        r_copy["video_url"] = v_url or r["video_url"]
        r_copy["preview_url"] = p_url or v_url or r["video_url"]
        r_copy["thumbnail_url"] = t_url or ""
        enriched.append(r_copy)
        
    return {"user_id": user_id, "status": "ready", "rides": enriched}
