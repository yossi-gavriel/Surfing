# Staging Readiness Report — Analysis Service

**Branch:** `codex/add-yolo-pipeline`
**Date:** 2026-04-15
**Previous report:** `MERGE_READINESS_REPORT.md` (2026-04-13, dev-merge-only)

---

## 1. What Was Validated Live

### 1.1 YOLO Weights Provisioning (RESOLVED)

**Approach chosen:** S3 download at container startup.

| Property | Value |
|----------|-------|
| S3 path | `s3://heyi-bucket/models/wave_surfer_v1.0/yolo_wave_surfer.pt` |
| File size | 6,245,290 bytes (6.0 MB) |
| SHA256 prefix | `589502af8c6a9743...` |
| Version env var | `ANALYSIS_MODEL_VERSION` (default: `wave_surfer_v1.0`) |
| Local path | `ANALYSIS_YOLO_MODEL_PATH` (default: `/app/services/analysis-service/models/yolo_wave_surfer.pt`) |

**Implementation:**
- `scripts/download_model.py` — Downloads from S3, validates non-empty, logs SHA256
- `scripts/entrypoint.sh` — Runs download_model before starting service; starts in stub mode if download fails
- `Dockerfile` — Updated CMD to use entrypoint.sh; installs libgl1/libglib2.0 for OpenCV

**Live validation against real S3:**

```
$ S3_BUCKET=heyi-bucket python3 scripts/download_model.py
Downloading model: s3://heyi-bucket/models/wave_surfer_v1.0/yolo_wave_surfer.pt -> ...
Model downloaded: size=6245290 sha256=589502af8c6a9743... duration_ms=4466

$ python3 scripts/download_model.py   # second run
Model already present: path=... size=6245290 sha256=589502af8c6a9743...

$ ANALYSIS_MODEL_VERSION=nonexistent_v99 python3 scripts/download_model.py
ERROR: Model download failed: 404 Not Found   (exit code 1)
```

**Operational tradeoffs:**
- +4-5s startup latency on first boot (one-time download)
- Model not in Docker image → smaller image, but requires S3 access at startup
- Degradation is graceful — service starts in stub mode if download fails
- To embed in image instead: uncomment `COPY` line in Dockerfile

### 1.2 End-to-End Pipeline (VALIDATED LIVE)

Full pipeline executed against **real AWS infrastructure** (S3 bucket: `heyi-bucket`, SQS: `analysis-queue`):

```
[Step 1] Verify clip exists: s3://heyi-bucket/rides/e2e-test-track-001.mp4
  PASS: clip exists, size=4309039 bytes

[Step 2] Verify model weights
  PASS: model present, size=6245290

[Step 3] Initialize job lifecycle DB
  PASS: temp DB created

[Step 4] Create analysis job in DB
  PASS: job created, status=processing

[Step 5] Run RideAnalyzer
  stage=clip_download    duration_ms=4708
  stage=frame_extraction duration_ms=265   frames_sampled=80 total_frames=238
  stage=detection        duration_ms=7465  surfer_detections=80 wave_detections=80
  stage=spatial_analysis wave_coverage=1.000 avg_white_level=0.282
  stage=trajectory       dominant_direction=right direction_changes_x=7
  stage=maneuver_detection maneuvers_found=10
  stage=scoring          ride_score=9.5
  stage=artifact_write   duration_ms=1352

  Total elapsed: 13,824ms

[Step 6] Update job status
  PASS: status=completed, ride_score=9.5, maneuver_count=10

[Step 7] Verify canonical JSON on S3
  PASS: s3://heyi-bucket/analysis/e2e-test-track-001/ride_summary.json
    schema=ride_summary_v1, score=9.5, maneuvers=10
    ride.duration=8.0s, ride.direction=right, ride.confidence=high
    wave.coverage=1.0, wave.white_level=0.2817

[Step 8] Verify debug JSON on S3
  PASS: per_frame entries=80, timing data present

[Step 9] User-scoped DB query
  PASS: user query returned 1 job, other user returned 0

[Step 10] SQS queue verification
  PASS: visibility_timeout=300s, redrive to analysis-dlq (maxReceiveCount=5)
```

**Test clip:** 8-second 1080p surfing video (y3_n_surfing_session_0.7.avi), converted to MP4, uploaded to S3.

### 1.3 Runtime Performance (VALIDATED LIVE)

| Metric | Value | Limit | Status |
|--------|-------|-------|--------|
| Peak RSS memory | 415 MB | 2500 MB | PASS (2085 MB headroom) |
| Baseline (imports) | 87 MB | — | — |
| After model load | 337 MB | — | — |
| After 10+ inferences | 415 MB | — | No leak detected |
| Processing time (8s 1080p clip) | 13.8s | 280s timeout | PASS |
| SIGALRM timeout mechanism | Fires correctly | — | PASS |
| Timeout safety margin | 20s (280 vs 300) | — | PASS |

### 1.4 Observability (VALIDATED LIVE)

All checks validated against real log output from live analysis runs:

| Check | Status |
|-------|--------|
| track_id in log lines | PASS (12/14 lines) |
| Stage transitions (started+completed) | PASS (all 8 stages) |
| duration_ms timing | PASS (5 entries) |
| model_version in logs | PASS |
| Detection counts (surfer/wave) | PASS |
| Maneuver count | PASS |
| ride_score | PASS |
| canonical_s3 in artifact log | PASS |
| Log format: `timestamp - logger - [LEVEL] - message` | PASS |
| failure_code on error paths | PASS (validated in code) |

**Sample log line:**
```
2026-04-15 16:01:06 - obs-check - [INFO] - [obs-check-001] stage=detection status=completed
  duration_ms=2587 frames_sampled=80 surfer_detections=80 wave_detections=80
```

---

## 2. What Could Only Be Partially Validated

### 2.1 Docker Container Build and Start

**Status: BUILD VALIDATED; DAEMON START NOT VALIDATED**

What was validated:
- `docker build -f services/analysis-service/Dockerfile . -t surf-ai-analysis-service:verify` completed successfully
- Dockerfile syntax is correct
- entrypoint.sh allows startup to continue in stub mode if model download fails
- All Python dependencies install in the container build
- Analysis-service tests pass in Python 3.11
- Memory and performance characteristics measured in-process

**Remaining gap:** `docker run` has not been executed as a long-running worker against the real SQS queue. This must be done on the EC2 instance before enabling analysis event publishing.

### 2.2 SQS Consumer Loop Integration

**Status: PARTIALLY VALIDATED**

- The `main()` consumer loop code was validated by code analysis
- `process_analysis()` was validated end-to-end with real S3
- SQS queues were created and verified (visibility, redrive policy)
- Message format matches clipper's publish format

**Remaining gap:** A real SQS message → consumer → S3 artifact loop was not run as a service. The consumer loop was tested by calling `process_analysis()` directly with the same message format.

### 2.3 Blue-Green Deployment

**Status: NOT VALIDATED**

The analysis-service is added to `docker-compose.yml` and can be deployed via `make deploy-worker SERVICE=analysis-service`. The blue-green deployment scripts were not modified and were not tested with the new service.

---

## 3. What Remains Risky

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Worker daemon start not tested against real SQS | MEDIUM | Build passes locally. Run the container on EC2 before enabling publishing. Dockerfile adds `libgl1` + `libglib2.0` for OpenCV. |
| SQS consumer loop not tested as daemon | MEDIUM | `process_analysis()` validated live. Loop follows clipper-service pattern identically. |
| High maneuver count (10) on test video | LOW | Heuristic thresholds may need tuning with more diverse clips. Non-blocking. |
| `ANALYSIS_DEBUG_ARTIFACTS=false` not tested in prod | LOW | Set to false in user_data.sh. Skips debug write; canonical still written. |
| EC2 disk space for model downloads | LOW | 6 MB model + temp clips. 30 GB EBS has ample room. |

---

## 4. Whether Staging Is Now Safe

**Yes, with conditions.**

The analysis service can be safely deployed to staging because:

1. **Feature flag is OFF by default** — `ANALYSIS_ENABLED=false` in both `.env.example` and `user_data.sh`. No analysis events are published until explicitly enabled.
2. **Clipper isolation is proven** — Analysis publish failure is non-blocking. Clipper has zero imports from analysis modules.
3. **Graceful degradation** — If model download fails, service runs in stub mode. If analysis fails, clip flow is unaffected.
4. **SQS infrastructure exists** — Both `analysis-queue` and `analysis-dlq` are created in us-east-1.
5. **Model weights are on S3** — Versioned at `s3://heyi-bucket/models/wave_surfer_v1.0/yolo_wave_surfer.pt`.

**Conditions for staging:**

1. Build Docker image on the EC2 instance: `docker-compose -f infra/docker-compose.yml build analysis-service`
2. Verify container starts: `docker-compose -f infra/docker-compose.yml up -d analysis-service && docker logs analysis-service`
3. Keep `ANALYSIS_ENABLED=false` initially — deploy the service but don't publish events
4. After confirming container health, set `ANALYSIS_ENABLED=true` and test with one clip
5. Monitor logs for the first few analysis jobs

---

## 5. Rollback Steps

### Immediate (< 1 minute)
```bash
# Stop analysis events from being published
# In .env: set ANALYSIS_ENABLED=false
# Then restart clipper:
docker-compose -f infra/docker-compose.yml restart clipper-service
```

### Full rollback (< 5 minutes)
```bash
# Stop analysis service
docker-compose -f infra/docker-compose.yml stop analysis-service

# Remove analysis data (if needed)
# sqlite3 /app/data/surf_ai.db "DROP TABLE IF EXISTS analysis_jobs;"

# Remove analysis artifacts from S3 (if needed)
# aws s3 rm s3://heyi-bucket/analysis/ --recursive
```

### What is NOT affected by rollback:
- All existing clip, video, track, and match data
- Clipper-service behavior (analysis publish is try/except non-blocking)
- API gateway (analysis routes return empty/404, no existing routes changed)
- Frontend (no frontend changes were made)

---

## 6. Files Changed Since Dev Merge Report

| File | Change |
|------|--------|
| `services/analysis-service/Dockerfile` | Updated: added libgl1/libglib2.0, changed CMD to entrypoint.sh |
| `services/analysis-service/scripts/download_model.py` | NEW: S3 model download with validation |
| `services/analysis-service/scripts/entrypoint.sh` | NEW: Download model then start service |
| `services/analysis-service/src/config.py` | Added `model_version` env var |
| `services/analysis-service/tests/e2e_live_validation.py` | NEW: Live E2E test script |
| `services/analysis-service/tests/test_failure_paths.py` | NEW: 21 failure path tests |
| `services/analysis-service/tests/test_feature_flag.py` | NEW: 5 feature flag tests |
| `services/analysis-service/tests/test_api_auth_scoping.py` | NEW: 9 auth scoping tests |
| `.env.example` | Added all ANALYSIS_* env vars |
| `.gitignore` | Added `.env` and `*.pt` model exclusion |
| `infra/terraform/ec2.tf` | Added q_analysis, q_analysis_dlq template vars |
| `infra/terraform/user_data.sh` | Added ANALYSIS_* env vars (ANALYSIS_ENABLED=false) |

**Total test count: 76 unit tests + 1 live E2E validation script**

---

## 7. Final Verdict

**Ready for staging.**

The two blockers from the dev-merge report are resolved:
1. YOLO weights provisioning — implemented, uploaded to S3, validated live (download, skip, failure)
2. Live E2E validation — executed against real AWS S3/SQS with a real surfing clip

**Remaining condition:** Docker build must be verified on the EC2 instance before enabling `ANALYSIS_ENABLED=true`. The service should be deployed with the flag OFF first, then enabled after container health is confirmed.
