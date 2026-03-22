# Surf AI System

A multi-service AI video pipeline that processes surfing videos from up to 3 cameras simultaneously, detects surfers, and assigns videos to users.

This repository is structured for scalability, driving configuration from a central file and supporting parallel, stateless processing. Each microservice scales independently via SQS orchestration.

## Completed Services

1. **Ingestion Service**: Connects to dynamic RTSP camera streams, chunking video into segments using FFmpeg, uploading to S3, and pushing notifications to an SQS queue.

## Setup Requirements

- Docker and Docker Compose
- Python 3.11
- FFmpeg installed locally (if running locally native without Docker)

Create a `.env` file from `.env.example`:
```bash
cp .env.example .env
```
Fill in your AWS details and region in `.env`.

Review configuration in `config/cameras.json` to define your cameras. The default behavior is standardizing RTSP endpoints to ingest stream data into isolated files.

## Running Locally (Docker)

To build and start the ingestion service:

```bash
make build
make up
```

To view logs:
```bash
make logs
```

To stop:
```bash
make down
```

## Running Locally (Native)

First, install dependencies:
```bash
pip install -r services/ingestion-service/requirements.txt
```

Ensure `ffmpeg` is loaded in your path. Then, run the local script:
```bash
make run-local
```
*(On windows, you may prefer running Python natively from the project root instead of `.sh`:)*
```powershell
$env:PYTHONPATH="."
$env:CAMERAS_CONFIG_PATH=".\config\cameras.json"
python services/ingestion-service/src/main.py
```

## How to test ingestion manually

1. You can run a test RTSP server or use a public RTSP stream url. Change `config/cameras.json` to point to a valid `rtsp_url`.
2. Fill your `.env` with a real AWS Region, S3 Bucket, and SQS Queue in your AWS account. Ensure you have AWS CLI credentials set up if running locally, or rely on IAM Roles if deployed to AWS ECS/EKS.
3. Start the service. Observe 10-second video segments (`*.ts`) being created in local temporary storage.
4. Verify the segments are being successfully uploaded to your `S3_BUCKET` in the `raw/{camera_id}/YYYY/MM/DD/HH/` partitioned structure.
5. Check standard output logs dynamically showing AWS SQS messages being dispatched correctly representing the completed ingestions.
