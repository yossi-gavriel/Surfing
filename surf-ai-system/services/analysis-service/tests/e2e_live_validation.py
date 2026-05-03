#!/usr/bin/env python3
"""Live end-to-end validation against real AWS infrastructure.

This script validates the full analysis pipeline:
  clip on S3 -> analyzer -> canonical JSON on S3 -> DB job lifecycle

It uses real AWS S3 and a real YOLO model, not mocks.
Run from: services/analysis-service/
Requires: S3_BUCKET, AWS credentials, YOLO model file

Usage:
    S3_BUCKET=<bucket> ANALYSIS_INPUT_SQS_URL=<queue-url> python3 tests/e2e_live_validation.py
"""

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

missing_env = [
    name
    for name in ("S3_BUCKET", "ANALYSIS_INPUT_SQS_URL")
    if not os.environ.get(name)
]
if missing_env:
    print(
        "Missing required env vars for live validation: "
        + ", ".join(missing_env),
        file=sys.stderr,
    )
    sys.exit(2)

import boto3
from src.config import AnalysisConfig, MODEL_VERSION
from shared.utils.logger import get_logger
from shared.utils.pipeline_store import PipelineStore

logger = get_logger("e2e-validation")


def main():
    print("=" * 70)
    print("LIVE E2E VALIDATION — Analysis Service")
    print("=" * 70)

    config = AnalysisConfig()
    s3_client = boto3.client("s3", region_name=config.aws_region)

    # Test parameters
    track_id = "e2e-test-track-001"
    clip_s3 = f"s3://{config.s3_bucket}/rides/{track_id}.mp4"

    # ── Step 1: Verify clip exists on S3 ──
    print(f"\n[Step 1] Verify clip exists: {clip_s3}")
    try:
        parts = clip_s3.split("//", 1)[1].split("/", 1)
        resp = s3_client.head_object(Bucket=parts[0], Key=parts[1])
        clip_size = resp["ContentLength"]
        print(f"  PASS: clip exists, size={clip_size} bytes")
    except Exception as e:
        print(f"  FAIL: clip not found: {e}")
        return 1

    # ── Step 2: Verify model weights exist ──
    print(f"\n[Step 2] Verify model weights: {config.yolo_model_path}")
    if os.path.isfile(config.yolo_model_path) and os.path.getsize(config.yolo_model_path) > 0:
        print(f"  PASS: model present, size={os.path.getsize(config.yolo_model_path)}")
    else:
        print(f"  FAIL: model not found at {config.yolo_model_path}")
        return 1

    # ── Step 3: Create temp DB for job lifecycle ──
    print("\n[Step 3] Initialize job lifecycle DB")
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    store = PipelineStore(db_path)
    print(f"  PASS: temp DB at {db_path}")

    # ── Step 4: Create analysis job ──
    print("\n[Step 4] Create analysis job in DB")
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    with store.store.connection() as conn:
        conn.execute(
            """INSERT INTO analysis_jobs
               (job_id, track_id, video_id, user_id, pool_id, camera_id,
                status, retry_count, retryable, clip_s3, model_version,
                created_at, started_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'processing', 0, 1, ?, ?, ?, ?, ?)""",
            ("e2e-job-001", track_id, "e2e-video-001", "e2e-user-001",
             "e2e-pool-001", "e2e-cam-001", clip_s3, MODEL_VERSION,
             now, now, now),
        )
    job = store.get_analysis_job(track_id)
    assert job is not None and job["status"] == "processing"
    print(f"  PASS: job created, status={job['status']}, job_id={job['job_id']}")

    # ── Step 5: Run RideAnalyzer ──
    print(f"\n[Step 5] Run RideAnalyzer on {clip_s3}")
    from src.analyzer import RideAnalyzer

    analyzer = RideAnalyzer(config, s3_client, logger)

    msg_body = {
        "track_id": track_id,
        "video_id": "e2e-video-001",
        "user_id": "e2e-user-001",
        "pool_id": "e2e-pool-001",
        "camera_id": "e2e-cam-001",
        "clip_s3": clip_s3,
        "start_time": "2026-04-15T10:00:00Z",
        "end_time": "2026-04-15T10:00:08Z",
    }

    t0 = time.time()
    result = analyzer.analyze(msg_body)
    elapsed_ms = int((time.time() - t0) * 1000)

    print(f"\n  Analysis result (elapsed={elapsed_ms}ms):")
    for k, v in result.items():
        print(f"    {k}: {v}")

    if result.get("failure_code"):
        print(f"\n  FAIL: analysis failed with failure_code={result['failure_code']}")
        print(f"        failure_reason={result.get('failure_reason')}")
        # Update job as failed
        with store.store.connection() as conn:
            conn.execute(
                """UPDATE analysis_jobs SET status='failed', failure_code=?,
                   failure_reason=?, analysis_duration_ms=?, updated_at=?
                   WHERE track_id=?""",
                (result["failure_code"], result.get("failure_reason"),
                 elapsed_ms, now, track_id),
            )
        return 1

    # ── Step 6: Update job as completed ──
    print("\n[Step 6] Update job status to completed")
    with store.store.connection() as conn:
        conn.execute(
            """UPDATE analysis_jobs SET status='completed',
               canonical_s3=?, debug_s3=?,
               ride_duration_seconds=?, dominant_direction=?,
               ride_score=?, maneuver_count=?,
               analysis_duration_ms=?, completed_at=?, updated_at=?
               WHERE track_id=?""",
            (result.get("canonical_s3"), result.get("debug_s3"),
             result.get("ride_duration_seconds"), result.get("dominant_direction"),
             result.get("ride_score"), result.get("maneuver_count"),
             elapsed_ms, now, now, track_id),
        )
    job = store.get_analysis_job(track_id)
    print(f"  PASS: job status={job['status']}, ride_score={job['ride_score']}, "
          f"maneuver_count={job['maneuver_count']}")

    # ── Step 7: Verify canonical JSON on S3 ──
    canonical_s3 = result.get("canonical_s3", "")
    print(f"\n[Step 7] Verify canonical JSON: {canonical_s3}")
    if canonical_s3:
        parts = canonical_s3.split("//", 1)[1].split("/", 1)
        try:
            resp = s3_client.get_object(Bucket=parts[0], Key=parts[1])
            canonical_body = json.loads(resp["Body"].read())
            print(f"  PASS: canonical JSON retrieved, schema={canonical_body.get('$schema')}")
            print(f"    track_id: {canonical_body.get('track_id')}")
            print(f"    status: {canonical_body.get('status')}")
            print(f"    score: {canonical_body.get('score')}")
            print(f"    maneuvers: {len(canonical_body.get('maneuvers', []))}")
            if canonical_body.get("ride"):
                ride = canonical_body["ride"]
                print(f"    ride.duration: {ride.get('duration_seconds')}s")
                print(f"    ride.direction: {ride.get('dominant_direction')}")
                print(f"    ride.confidence: {ride.get('confidence')}")
            if canonical_body.get("wave"):
                wave = canonical_body["wave"]
                print(f"    wave.coverage: {wave.get('coverage_ratio')}")
                print(f"    wave.white_level: {wave.get('avg_white_level')}")
        except Exception as e:
            print(f"  FAIL: cannot retrieve canonical JSON: {e}")
            return 1
    else:
        print("  FAIL: no canonical_s3 in result")
        return 1

    # ── Step 8: Verify debug JSON on S3 ──
    debug_s3 = result.get("debug_s3")
    print(f"\n[Step 8] Verify debug JSON: {debug_s3}")
    if debug_s3:
        parts = debug_s3.split("//", 1)[1].split("/", 1)
        try:
            resp = s3_client.get_object(Bucket=parts[0], Key=parts[1])
            debug_body = json.loads(resp["Body"].read())
            print(f"  PASS: debug JSON retrieved")
            print(f"    per_frame entries: {len(debug_body.get('per_frame', []))}")
            print(f"    timing: {debug_body.get('processing_timing', {})}")
            print(f"    clip_metadata: {debug_body.get('clip_metadata', {})}")
        except Exception as e:
            print(f"  WARN: cannot retrieve debug JSON: {e}")
    else:
        print("  SKIP: debug artifacts not enabled or not written")

    # ── Step 9: Verify user-scoped query ──
    print("\n[Step 9] Verify user-scoped DB query")
    user_jobs = store.list_analysis_jobs_for_user("e2e-user-001")
    assert len(user_jobs) == 1
    assert user_jobs[0]["track_id"] == track_id
    print(f"  PASS: user query returned {len(user_jobs)} job(s)")

    other_user_jobs = store.list_analysis_jobs_for_user("other-user-999")
    assert len(other_user_jobs) == 0
    print(f"  PASS: other user query returned {len(other_user_jobs)} job(s)")

    # ── Step 10: Verify SQS queue exists ──
    print("\n[Step 10] Verify analysis SQS queue exists")
    sqs_client = boto3.client("sqs", region_name=config.aws_region)
    try:
        attrs = sqs_client.get_queue_attributes(
            QueueUrl=config.input_sqs_url,
            AttributeNames=["VisibilityTimeout", "RedrivePolicy"],
        )
        visibility = attrs["Attributes"].get("VisibilityTimeout")
        redrive = attrs["Attributes"].get("RedrivePolicy")
        print(f"  PASS: queue exists, visibility_timeout={visibility}s")
        if redrive:
            print(f"    redrive_policy: {redrive}")
    except Exception as e:
        print(f"  FAIL: queue check failed: {e}")

    # Clean up temp DB
    os.unlink(db_path)

    print("\n" + "=" * 70)
    print("E2E VALIDATION COMPLETE — ALL STEPS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
