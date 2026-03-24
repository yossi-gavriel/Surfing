#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${SURF_AI_STATE_DIR:-$PROJECT_ROOT/.deploy}"
SLOTS_DIR="$STATE_DIR/slots"
ACTIVE_SLOT_FILE="$STATE_DIR/active_slot"
HISTORY_FILE="$STATE_DIR/history.log"
DB_VOLUME="${SURF_AI_DB_VOLUME:-infra_db-data}"
NGINX_UPSTREAMS_FILE="${SURF_AI_NGINX_UPSTREAMS_FILE:-/etc/nginx/conf.d/surf-ai-upstreams.conf}"
NGINX_RELOAD_CMD="${SURF_AI_NGINX_RELOAD_CMD:-systemctl reload nginx}"

ensure_state_dirs() {
  mkdir -p "$STATE_DIR" "$SLOTS_DIR"
}

timestamp_version() {
  date -u +"%Y%m%d%H%M%S"
}

git_short_sha() {
  git -C "$PROJECT_ROOT" rev-parse --short HEAD 2>/dev/null || echo "nogit"
}

current_active_slot() {
  if [[ -f "$ACTIVE_SLOT_FILE" ]]; then
    tr -d '[:space:]' < "$ACTIVE_SLOT_FILE"
    return
  fi
  echo ""
}

inactive_slot() {
  local active_slot="${1:-}"
  if [[ "$active_slot" == "blue" ]]; then
    echo "green"
    return
  fi
  echo "blue"
}

slot_api_port() {
  local slot="$1"
  if [[ "$slot" == "green" ]]; then
    echo "8002"
    return
  fi
  echo "8001"
}

slot_frontend_port() {
  local slot="$1"
  if [[ "$slot" == "green" ]]; then
    echo "4202"
    return
  fi
  echo "4201"
}

slot_api_container() {
  echo "api-gateway-$1"
}

slot_frontend_container() {
  echo "frontend-$1"
}

slot_metadata_file() {
  echo "$SLOTS_DIR/$1.env"
}

load_slot_metadata() {
  local slot="$1"
  local metadata_file
  metadata_file="$(slot_metadata_file "$slot")"
  if [[ ! -f "$metadata_file" ]]; then
    return 1
  fi
  # shellcheck disable=SC1090
  source "$metadata_file"
}

write_slot_metadata() {
  local slot="$1"
  local version="$2"
  local api_image="$3"
  local frontend_image="$4"
  cat > "$(slot_metadata_file "$slot")" <<EOF
SLOT=$slot
VERSION=$version
API_IMAGE=$api_image
FRONTEND_IMAGE=$frontend_image
API_CONTAINER=$(slot_api_container "$slot")
FRONTEND_CONTAINER=$(slot_frontend_container "$slot")
API_PORT=$(slot_api_port "$slot")
FRONTEND_PORT=$(slot_frontend_port "$slot")
UPDATED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF
}

upsert_env_key() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp_file
  tmp_file="$(mktemp)"
  if [[ -f "$file" ]]; then
    grep -v "^${key}=" "$file" > "$tmp_file" || true
  fi
  printf "%s=%s\n" "$key" "$value" >> "$tmp_file"
  mv "$tmp_file" "$file"
}

build_image() {
  local image_tag="$1"
  local dockerfile="$2"
  echo "Building $image_tag from $dockerfile"
  docker build -t "$image_tag" -f "$PROJECT_ROOT/$dockerfile" "$PROJECT_ROOT"
}

remove_container_if_exists() {
  local container_name="$1"
  if docker ps -a --format '{{.Names}}' | grep -Fxq "$container_name"; then
    docker rm -f "$container_name" >/dev/null
  fi
}

run_slot_containers() {
  local slot="$1"
  local version="$2"
  local api_image="$3"
  local frontend_image="$4"
  local api_container
  local frontend_container
  local api_port
  local frontend_port

  api_container="$(slot_api_container "$slot")"
  frontend_container="$(slot_frontend_container "$slot")"
  api_port="$(slot_api_port "$slot")"
  frontend_port="$(slot_frontend_port "$slot")"

  remove_container_if_exists "$api_container"
  remove_container_if_exists "$frontend_container"

  docker run -d \
    --name "$api_container" \
    --restart unless-stopped \
    --env-file "$PROJECT_ROOT/.env" \
    -e APP_VERSION="$version" \
    -e DEPLOYMENT_VERSION="$version" \
    -v "$DB_VOLUME:/app/data" \
    -p "${api_port}:8000" \
    "$api_image" >/dev/null

  docker run -d \
    --name "$frontend_container" \
    --restart unless-stopped \
    -e APP_VERSION="$version" \
    -e DEPLOYMENT_VERSION="$version" \
    -p "${frontend_port}:80" \
    "$frontend_image" >/dev/null
}

wait_for_api_health() {
  local slot="$1"
  local api_port
  api_port="$(slot_api_port "$slot")"
  local health_url="http://127.0.0.1:${api_port}/health"

  for attempt in $(seq 1 30); do
    if curl -fsS "$health_url" >/dev/null; then
      return 0
    fi
    sleep 2
  done

  echo "API health check failed for slot $slot ($health_url)" >&2
  docker logs --tail 200 "$(slot_api_container "$slot")" >&2 || true
  return 1
}

wait_for_frontend() {
  local slot="$1"
  local frontend_port
  frontend_port="$(slot_frontend_port "$slot")"
  local frontend_url="http://127.0.0.1:${frontend_port}/"

  for attempt in $(seq 1 20); do
    if curl -fsSI "$frontend_url" >/dev/null; then
      return 0
    fi
    sleep 1
  done

  echo "Frontend check failed for slot $slot ($frontend_url)" >&2
  docker logs --tail 100 "$(slot_frontend_container "$slot")" >&2 || true
  return 1
}

write_upstreams_for_slot() {
  local slot="$1"
  cat > "$NGINX_UPSTREAMS_FILE" <<EOF
upstream api_upstream {
    server 127.0.0.1:$(slot_api_port "$slot");
}

upstream frontend_upstream {
    server 127.0.0.1:$(slot_frontend_port "$slot");
}
EOF
}

switch_traffic_to_slot() {
  local slot="$1"
  write_upstreams_for_slot "$slot"
  nginx -t
  sh -c "$NGINX_RELOAD_CMD"
  printf "%s" "$slot" > "$ACTIVE_SLOT_FILE"
}

stop_slot_containers() {
  local slot="$1"
  docker stop "$(slot_api_container "$slot")" "$(slot_frontend_container "$slot")" >/dev/null 2>&1 || true
}

start_slot_containers() {
  local slot="$1"
  docker start "$(slot_api_container "$slot")" "$(slot_frontend_container "$slot")" >/dev/null
}

record_history() {
  local action="$1"
  local slot="$2"
  local version="$3"
  printf "%s action=%s slot=%s version=%s\n" \
    "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
    "$action" \
    "$slot" \
    "$version" >> "$HISTORY_FILE"
}
