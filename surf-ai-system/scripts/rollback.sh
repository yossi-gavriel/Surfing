#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./deploy_helpers.sh
source "$SCRIPT_DIR/deploy_helpers.sh"

ensure_state_dirs

ACTIVE_SLOT="$(current_active_slot)"
if [[ -z "$ACTIVE_SLOT" ]]; then
  echo "No active slot recorded; cannot roll back." >&2
  exit 1
fi

TARGET_SLOT="$(inactive_slot "$ACTIVE_SLOT")"
if ! load_slot_metadata "$TARGET_SLOT"; then
  echo "No previous deployment metadata found for slot $TARGET_SLOT" >&2
  exit 1
fi

echo "Rolling back to slot $TARGET_SLOT (version $VERSION)"

start_slot_containers "$TARGET_SLOT"
wait_for_api_health "$TARGET_SLOT"
wait_for_frontend "$TARGET_SLOT"
switch_traffic_to_slot "$TARGET_SLOT"
record_history "rollback" "$TARGET_SLOT" "$VERSION"
stop_slot_containers "$ACTIVE_SLOT"

upsert_env_key "$PROJECT_ROOT/.env" "APP_VERSION" "$VERSION"
upsert_env_key "$PROJECT_ROOT/.env" "DEPLOYMENT_VERSION" "$VERSION"
if [[ -f "$PROJECT_ROOT/infra/.env" ]]; then
  upsert_env_key "$PROJECT_ROOT/infra/.env" "APP_VERSION" "$VERSION"
  upsert_env_key "$PROJECT_ROOT/infra/.env" "DEPLOYMENT_VERSION" "$VERSION"
fi

echo "Rollback complete"
echo "  version: $VERSION"
echo "  active_slot: $TARGET_SLOT"
