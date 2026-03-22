#!/bin/bash
set -e

echo "Starting Ingestion Service locally..."

# Setup environment variables if .env exists
if [ -f .env ]; then
  export $(cat .env | xargs)
fi

export PYTHONPATH=$(pwd)
export CAMERAS_CONFIG_PATH=$(pwd)/config/cameras.json

python services/ingestion-service/src/main.py
