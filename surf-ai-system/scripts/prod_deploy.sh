#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_helpers.sh
source "$SCRIPT_DIR/deploy_helpers.sh"

ensure_state_dirs

if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
  echo "Missing $PROJECT_ROOT/.env" >&2
  exit 1
fi

VERSION="${1:-$(timestamp_version)-$(git_short_sha)}"
ACTIVE_SLOT="$(current_active_slot)"
TARGET_SLOT="$(inactive_slot "$ACTIVE_SLOT")"
API_IMAGE="surf-ai/api-gateway:${VERSION}"
FRONTEND_IMAGE="surf-ai/frontend:${VERSION}"

echo "Deploying version $VERSION to slot $TARGET_SLOT"

build_image "$API_IMAGE" "services/api-gateway/Dockerfile"
build_image "$FRONTEND_IMAGE" "frontend/Dockerfile"

run_slot_containers "$TARGET_SLOT" "$VERSION" "$API_IMAGE" "$FRONTEND_IMAGE"
wait_for_api_health "$TARGET_SLOT"
wait_for_frontend "$TARGET_SLOT"

switch_traffic_to_slot "$TARGET_SLOT"
write_slot_metadata "$TARGET_SLOT" "$VERSION" "$API_IMAGE" "$FRONTEND_IMAGE"
record_history "deploy" "$TARGET_SLOT" "$VERSION"

upsert_env_key "$PROJECT_ROOT/.env" "APP_VERSION" "$VERSION"
upsert_env_key "$PROJECT_ROOT/.env" "DEPLOYMENT_VERSION" "$VERSION"
if [[ -f "$PROJECT_ROOT/infra/.env" ]]; then
  upsert_env_key "$PROJECT_ROOT/infra/.env" "APP_VERSION" "$VERSION"
  upsert_env_key "$PROJECT_ROOT/infra/.env" "DEPLOYMENT_VERSION" "$VERSION"
fi

if [[ -n "$ACTIVE_SLOT" && "$ACTIVE_SLOT" != "$TARGET_SLOT" ]]; then
  stop_slot_containers "$ACTIVE_SLOT"
fi

echo "Deployment complete"
echo "  version: $VERSION"
echo "  active_slot: $TARGET_SLOT"
echo "  api_health: http://127.0.0.1:$(slot_api_port "$TARGET_SLOT")/health"
