#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_BIN="${DOCKER_COMPOSE_BIN:-docker-compose}"
SERVICE_NAME="${1:-}"
PYTHON_BIN="${PYTHON_BIN:-}"

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  fi
fi

if [[ -z "$SERVICE_NAME" ]]; then
  echo "Usage: $0 <frame-processor|embedding-service|matching-service|clipper-service|ingestion-service>" >&2
  exit 1
fi

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  echo "Missing $PROJECT_ROOT/.env" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$PROJECT_ROOT/.env"
set +a

queue_url_for_service() {
  case "$1" in
    frame-processor) echo "${INPUT_SQS_URL:-}" ;;
    embedding-service) echo "${EMBEDDING_INPUT_SQS_URL:-}" ;;
    matching-service) echo "${MATCHING_INPUT_SQS_URL:-}" ;;
    clipper-service) echo "${CLIPPER_INPUT_SQS_URL:-}" ;;
    ingestion-service) echo "" ;;
    *) echo "" ;;
  esac
}

queue_counts_for_url() {
  local queue_url="$1"
  if [[ -n "$PYTHON_BIN" ]] && "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import boto3  # noqa: F401
PY
  then
    QUEUE_URL="$queue_url" "$PYTHON_BIN" - <<'PY'
import boto3
import os

queue_url = os.environ["QUEUE_URL"]
region = os.environ.get("AWS_REGION", "us-east-1")
client = boto3.client("sqs", region_name=region)
attrs = client.get_queue_attributes(
    QueueUrl=queue_url,
    AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
)["Attributes"]
visible = int(attrs.get("ApproximateNumberOfMessages", "0"))
inflight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", "0"))
print(f"{visible} {inflight}")
PY
    return
  fi

  if command -v aws >/dev/null 2>&1; then
    aws sqs get-queue-attributes \
      --queue-url "$queue_url" \
      --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
      --region "${AWS_REGION:-us-east-1}" \
      --query '[Attributes.ApproximateNumberOfMessages, Attributes.ApproximateNumberOfMessagesNotVisible]' \
      --output text
    return
  fi

  echo "Missing queue inspection dependency (python+boto3 or aws CLI)" >&2
  return 1
}

wait_for_safe_queue() {
  local queue_url="$1"
  if [[ -z "$queue_url" ]]; then
    return 0
  fi

  for attempt in $(seq 1 30); do
    local counts
    counts="$(queue_counts_for_url "$queue_url")"
    local visible inflight
    read -r visible inflight <<< "$counts"
    if [[ "$inflight" == "0" ]]; then
      echo "Queue is safe: visible=$visible inflight=$inflight"
      return 0
    fi
    echo "Waiting for in-flight work to drain: visible=$visible inflight=$inflight"
    sleep 2
  done

  echo "Queue did not become safe in time for $queue_url" >&2
  return 1
}

QUEUE_URL="$(queue_url_for_service "$SERVICE_NAME")"

echo "Stopping $SERVICE_NAME"
docker stop --time 90 "$SERVICE_NAME" >/dev/null 2>&1 || true

if [[ -n "$QUEUE_URL" ]]; then
  export QUEUE_URL
  wait_for_safe_queue "$QUEUE_URL"
fi

echo "Starting refreshed $SERVICE_NAME"
$COMPOSE_BIN -f "$PROJECT_ROOT/infra/docker-compose.yml" up -d --build "$SERVICE_NAME"
docker ps --format "table {{.Names}}\t{{.Status}}" | grep "$SERVICE_NAME"
