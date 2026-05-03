#!/bin/bash
set -e

# Download model weights from S3 if not already present
echo "[entrypoint] Checking model weights..."
if python3 -m scripts.download_model; then
    echo "[entrypoint] Model weights ready."
else
    DOWNLOAD_EXIT=$?
    echo "[entrypoint] WARNING: Model download failed (exit=$DOWNLOAD_EXIT). Service will start in stub mode."
fi

# Start the analysis service
echo "[entrypoint] Starting analysis service..."
exec python3 -m src.main
