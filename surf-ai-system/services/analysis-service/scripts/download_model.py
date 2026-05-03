#!/usr/bin/env python3
"""Download YOLO model weights from S3 at startup.

This script is the canonical model provisioning path. It runs before the
analysis service starts and ensures the model file is present and valid.

Usage:
    python -m scripts.download_model          # uses env vars
    python scripts/download_model.py          # direct invocation

Environment variables:
    S3_BUCKET               Required. The S3 bucket containing model artifacts.
    ANALYSIS_MODEL_VERSION  Model version directory in S3. Default: wave_surfer_v1.0
    ANALYSIS_YOLO_MODEL_PATH  Local path to write the model. Default: /app/services/analysis-service/models/yolo_wave_surfer.pt
    AWS_REGION              AWS region. Default: us-east-1

S3 layout:
    s3://{S3_BUCKET}/models/{ANALYSIS_MODEL_VERSION}/yolo_wave_surfer.pt

Exit codes:
    0  Model already present and valid, or download succeeded
    1  Download failed or model is invalid
"""

import hashlib
import os
import sys
import time


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    s3_bucket = os.environ.get("S3_BUCKET")
    model_version = os.environ.get("ANALYSIS_MODEL_VERSION", "wave_surfer_v1.0")
    local_path = os.environ.get(
        "ANALYSIS_YOLO_MODEL_PATH",
        "/app/services/analysis-service/models/yolo_wave_surfer.pt",
    )
    aws_region = os.environ.get("AWS_REGION", "us-east-1")

    if not s3_bucket:
        print("ERROR: S3_BUCKET not set — cannot download model", file=sys.stderr)
        return 1

    s3_key = f"models/{model_version}/yolo_wave_surfer.pt"

    # Check if model already exists and is non-empty
    if os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
        sha = _file_sha256(local_path)
        print(
            f"Model already present: path={local_path} "
            f"size={os.path.getsize(local_path)} sha256={sha[:16]}..."
        )
        return 0

    # Ensure directory exists
    model_dir = os.path.dirname(local_path)
    os.makedirs(model_dir, exist_ok=True)

    # Download from S3
    print(
        f"Downloading model: s3://{s3_bucket}/{s3_key} -> {local_path} "
        f"(region={aws_region})"
    )

    try:
        import boto3
        s3_client = boto3.client("s3", region_name=aws_region)

        t0 = time.time()
        s3_client.download_file(s3_bucket, s3_key, local_path)
        elapsed_ms = int((time.time() - t0) * 1000)

        size = os.path.getsize(local_path)
        if size == 0:
            print("ERROR: Downloaded model is 0 bytes", file=sys.stderr)
            os.remove(local_path)
            return 1

        sha = _file_sha256(local_path)
        print(
            f"Model downloaded: size={size} sha256={sha[:16]}... "
            f"duration_ms={elapsed_ms}"
        )
        return 0

    except Exception as e:
        print(f"ERROR: Model download failed: {e}", file=sys.stderr)
        # Clean up partial download
        if os.path.exists(local_path):
            os.remove(local_path)
        return 1


if __name__ == "__main__":
    sys.exit(main())
