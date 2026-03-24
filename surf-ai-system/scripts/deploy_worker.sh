#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_BIN="${DOCKER_COMPOSE_BIN:-docker-compose}"
SERVICE_NAME="${1:-}"

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

wait_for_safe_queue() {
  local queue_url="$1"
  if [[ -z "$queue_url" ]]; then
    return 0
  fi

  for attempt in $(seq 1 30); do
    local counts
    counts="$(python - <<'PY'
import boto3
import json
import os

queue_url = os.environ["QUEUE_URL"]
region = os.environ.get("AWS_REGION", "us-east-1")
client = boto3.client("sqs", region_name=region)
attrs = client.get_queue_attributes(
    QueueUrl=queue_url,
    AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
)["Attributes"]
print(json.dumps({
    "visible": int(attrs.get("ApproximateNumberOfMessages", "0")),
    "inflight": int(attrs.get("ApproximateNumberOfMessagesNotVisible", "0")),
}))
PY
)"
    local inflight
    inflight="$(COUNTS_JSON="$counts" python - <<'PY'
import json
import os
print(json.loads(os.environ["COUNTS_JSON"])["inflight"])
PY
)"
    if [[ "$inflight" == "0" ]]; then
      echo "Queue is safe: $counts"
      return 0
    fi
    echo "Waiting for in-flight work to drain: $counts"
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
