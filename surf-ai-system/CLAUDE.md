# CLAUDE.md - Surf AI System

## Project Overview

Surf AI System is an AI-powered video pipeline that processes surfing videos from up to 3 cameras simultaneously. It detects surfers in video streams, extracts facial embeddings, matches them to registered users, and creates personalized highlight clips. The architecture is event-driven with stateless microservices orchestrated through AWS SQS queues.

**Tech Stack:** Python (backend services), Angular 17 (frontend), FastAPI (API gateway), SQLite (database), Docker, AWS (SQS, S3, EC2), Terraform, Redis, FFmpeg

---

## Repository Structure

```
surf-ai-system/
├── config/                 # Camera configuration (cameras.json)
├── frontend/              # Angular 17 web application
├── infra/                 # Infrastructure as Code
│   ├── docker-compose.yml # Local development orchestration
│   └── terraform/         # AWS infrastructure definitions
├── services/              # 6 microservices
│   ├── ingestion-service/ # RTSP stream capture & video chunking
│   ├── frame-processor/   # Person detection & tracking (YOLOv8)
│   ├── embedding-service/ # Face embedding extraction (InsightFace)
│   ├── matching-service/  # Cosine similarity user matching
│   ├── clipper-service/   # FFmpeg-based highlight clip extraction
│   └── api-gateway/       # FastAPI REST API + JWT auth
├── shared/                # Shared utilities (logger, constants, DB helpers)
├── tests/                 # Test suite
├── scripts/               # Deployment and utility scripts
├── Makefile               # Build/deploy commands
└── README.md
```

---

## Pipeline Architecture

The system follows a linear event-driven pipeline. Each service consumes from one SQS queue and produces to the next:

```
Camera Streams
      │
      ▼
┌──────────────┐    SQS: video-chunks-queue
│  Ingestion   │ ──────────────────────────────►
│  Service     │
└──────────────┘
                    ┌─────────────────┐    SQS: tracks-queue
                    │ Frame Processor │ ──────────────────────►
                    │ (YOLOv8)        │
                    └─────────────────┘
                                          ┌──────────────┐    SQS: embeddings-queue
                                          │  Embedding   │ ──────────────────────►
                                          │ (InsightFace)│
                                          └──────────────┘
                                                              ┌──────────────┐    SQS: matching-queue
                                                              │  Matching    │ ──────────────────────►
                                                              │  Service     │
                                                              └──────────────┘
                                                                                  ┌──────────────┐
                                                                                  │   Clipper    │
                                                                                  │  (FFmpeg)    │
                                                                                  └──────────────┘
```

### SQS Queues
- `video-chunks-queue` - Ingestion -> Frame Processor
- `tracks-queue` - Frame Processor -> Embedding
- `embeddings-queue` - Embedding -> Matching
- `matching-queue` - Matching -> Clipper
- `clipper-queue` - Admin backfill jobs
- Each major queue has a corresponding Dead Letter Queue (DLQ)

### S3 Storage Layout
- `raw/{camera_id}/YYYY/MM/DD/HH/` - Raw 10-second video chunks (.ts)
- `keyframes/{storage_key}/{track_id}_{filename}.jpg` - Extracted keyframe crops
- `debug-frames/{storage_key}/` - Debug annotated frames
- `clips/{track_id}.mp4` - Final highlight clips

---

## Service Details

### 1. Ingestion Service (`services/ingestion-service/`)

Captures live RTSP camera streams and chunks them into segments.

- Connects to RTSP cameras via FFmpeg subprocess
- Segments video into 10-second `.ts` chunks using FFmpeg's segment muxer
- Uploads chunks to S3 with date-partitioned paths
- Publishes chunk metadata to `video-chunks-queue`
- Polls SQLite for dynamic camera configuration changes
- Implements worker lease guards for distributed leadership

### 2. Frame Processor (`services/frame-processor/`)

Detects and tracks persons (surfers) across video frames.

**Person Detection Algorithm:**
- Model: YOLOv8 nano (`yolov8n.pt`, 3.3M parameters)
- Input: Frames resized to 640x640 for inference
- Filters: confidence > 0.5, min bounding box area 500px², aspect ratio < 3.0
- Only class 0 (person) detections are kept
- Bounding boxes are rescaled back to original frame dimensions

**Multi-Object Tracking Algorithm (IoU Tracker):**
- Association: Intersection-over-Union (IoU) based matching between frames
- IoU threshold: 0.3, center distance threshold: 100px
- Max 10 simultaneously active tracks
- Velocity prediction with exponential smoothing (alpha=0.7) for bbox prediction
- Redis-backed tracker state with 5-second TTL for cross-chunk continuity
- Track validation: minimum 15 frames per track, composite score must exceed threshold

**Output:** Validated tracks with bounding box sequences and keyframe crops uploaded to S3.

### 3. Embedding Service (`services/embedding-service/`)

Extracts face embeddings from tracked surfers.

**Face Detection & Embedding Algorithm:**
- Model: InsightFace `buffalo_s` (ArcFace backbone, 512-dimensional embeddings)
- Runtime: ONNXRuntime (CPU or GPU)
- Face detection runs on keyframe crops from each track

**Quality Scoring:**
```
quality_score = (confidence * 0.45) + (face_size_score * 0.35) + (blur_score * 0.20)
```
- Pose validation: yaw and pitch must be < 30 degrees
- Blur detection: Laplacian variance, threshold 50.0
- Minimum face size: 40px

**Embedding Aggregation:**
- Select top-K keyframes (default K=20) by quality score
- Extract 512-dim embedding from each
- L2-normalize each vector
- Average the normalized vectors to produce one aggregated embedding per track
- Store in SQLite `video_embeddings` table

### 4. Matching Service (`services/matching-service/`)

Matches track embeddings against registered user embeddings.

**Matching Algorithm:**
```
For each user in the database:
  1. Compute cosine similarity between track embedding and all user reference embeddings
  2. best_similarity = max similarity across this user's embeddings
  3. second_best_similarity = max similarity across all OTHER users

Decision logic:
  IF best_similarity >= SIMILARITY_THRESHOLD
     AND (best_similarity - second_best_similarity) >= MARGIN_THRESHOLD
  THEN → MATCH (assign track to this user)
  ELSE → NO MATCH
```

- Thresholds are configurable at runtime via `system_config` table
- Scoring: composite of distance statistics, consistency, and confidence
- Ranking: sorted by (best_similarity, final_score, aggregated_distance)
- Match records stored in SQLite `matches` table with full decision metadata

### 5. Clipper Service (`services/clipper-service/`)

Creates highlight clips from matched tracks.

- Receives match messages with track timestamps and source video references
- Downloads source video chunks from S3
- Uses FFmpeg to extract the time segment where the matched surfer appears
- Produces MP4 clips uploaded to S3 at `clips/{track_id}.mp4`

### 6. API Gateway (`services/api-gateway/`)

FastAPI-based REST API serving the frontend and admin operations.

**Authentication:** JWT tokens with role-based access (user vs admin)

**API Endpoints:**

| Route | Method | Description |
|-------|--------|-------------|
| `/auth/signup` | POST | Register new user |
| `/auth/login` | POST | Authenticate, returns JWT |
| `/auth/refresh` | POST | Refresh token |
| `/users/me` | GET | Current user profile |
| `/users/me/pool` | GET | Active pool context |
| `/users/me/pool/select` | POST | Switch active pool |
| `/users/me/reference-images` | GET | List uploaded face images |
| `/users/me/reference-images` | POST | Upload face reference image |
| `/users/me/videos` | GET | Matched video history |
| `/videos/{video_id}` | GET | Video details with matches |
| `/admin/videos` | GET | All videos (pool-scoped) |
| `/admin/videos/{video_id}/assign` | POST | Manually assign video to user |
| `/admin/cameras` | GET | List cameras |
| `/admin/cameras` | POST | Register camera |
| `/admin/cameras/{camera_id}` | PUT | Update camera config |
| `/admin/config` | GET | System configuration |
| `/admin/config` | PUT | Update config (audit trail) |
| `/admin/config/rollback` | POST | Revert config changes |
| `/admin/matches/backfill` | POST | Re-trigger matching for user |
| `/status` | GET | Service readiness (all components) |
| `/health` | GET | Minimal liveness probe |

---

## Frontend (`frontend/`)

**Framework:** Angular 17 with standalone components and signals

**Pages:**
- `login/` - User authentication
- `upload-face/` - Reference face image submission for matching
- `my-videos/` - User's matched video history with presigned S3 URLs
- `admin/` - System management dashboard (cameras, config, debug tools, video assignment)

**Key Services:**
- `auth.service.ts` - JWT token management and refresh
- `auth.guard.ts` - Role-based route protection (user/admin)

**Build & Deployment:** Multi-stage Docker build -> static assets served by Nginx reverse proxy

---

## Database Schema (SQLite)

### Core Tables

| Table | Purpose |
|-------|---------|
| `users` | User accounts with role and pool association |
| `pools` | Group/project containers for organizing users |
| `videos` | Source video metadata (status: uploaded/processing/completed/failed) |
| `video_embeddings` | Aggregated 512-dim track embeddings |
| `video_frame_embeddings` | Per-frame face embeddings with quality scores |
| `user_embeddings` | Reference embeddings uploaded by users for matching |
| `matches` | Match records linking tracks to users with decision metadata |
| `workers_leases` | Distributed leader election (TTL-based) |
| `jobs` | Job tracking for idempotency (prevents duplicate processing) |
| `worker_metrics` | Performance counters per service instance |
| `system_config` | Runtime configuration (thresholds, retention policies) |

### Key Indexes
- `matches`: (user_id, created_at), (track_id, unique), (pool_id, video_id)
- `videos`: (status), (pool_id, created_at)
- `video_embeddings`: (video_id, track_id)
- `user_embeddings`: (user_id, id)

---

## Shared Utilities (`shared/`)

- **Logger:** Structured logging with service-name tagging
- **Constants:** Queue names, S3 path templates, default thresholds
- **DB Helpers:** SQLite connection management, migration utilities
- **SQS Helpers:** Message send/receive/delete with retry logic

---

## Infrastructure

### Docker Compose (`infra/docker-compose.yml`)
- Services: Redis, 6 microservices, frontend
- Shared volumes: SQLite DB, ingestion temp storage, keyframe storage
- Memory limits: frame-processor & embedding-service at 2.5GB, matching at 512MB
- Ports: API gateway (8000), frontend (4200)
- Restart policy: `unless-stopped`

### Terraform (`infra/terraform/`)
- **EC2:** Ubuntu 22.04 instance
- **S3:** Video storage bucket (versioning disabled)
- **SQS:** 5 queues + DLQs with configurable visibility timeouts
- **IAM:** Service roles with least-privilege S3/SQS access
- **Route53:** DNS with SSL/TLS (Let's Encrypt via Certbot)
- **EIP:** Static IP for the EC2 instance
- **State:** S3 backend for Terraform state management

---

## Operational Patterns

### Worker Safety
- **Graceful Shutdown:** SIGTERM/SIGINT signal handlers with in-flight message draining
- **Worker Leases:** SQLite-backed distributed leadership with TTL expiration
- **Idempotency:** Job key deduplication prevents reprocessing
- **Dead Letter Queues:** Messages exceeding max retries routed to DLQ
- **Metrics:** Per-worker counters (processed, failures, retries, dead-lettered)

### Scaling Model
- Each service scales independently based on SQS queue depth
- Stateless workers: all state lives in SQLite, S3, or Redis
- Lease system prevents dual-leadership conflicts
- Horizontal scaling by adding more worker containers per service

---

## Development Commands

```bash
make build        # Build all Docker images
make up           # Start all services
make down         # Stop all services
make logs         # Tail ingestion-service logs
make run-local    # Run ingestion natively (requires FFmpeg in PATH)
```

## Key Configuration

Configuration is driven by environment variables (see `.env.example`):
- `CHUNK_DURATION=10` - Video segment length in seconds
- `YOLO_MODEL=yolov8n.pt` - Detection model
- `INSIGHTFACE_MODEL=buffalo_s` - Face recognition model
- `MIN_FACE_SIZE=40` - Minimum face size in pixels
- `MIN_CONFIDENCE=0.5` - Detection confidence threshold
- `MIN_BLUR_SCORE=50.0` - Laplacian blur threshold
- `CAMERA_POLL_INTERVAL=10` - Camera config refresh interval

Matching thresholds are stored in `system_config` table and adjustable at runtime via the admin API.
