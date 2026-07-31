#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a; source "$PROJECT_DIR/.env"; set +a
fi

CONTAINER="${1:-${CONTAINER_NAME:-playground-codex-test-emr-integration-data-parsing-worker_DEV-1}}"
OUTPUT="${2:-${LOG_OUTPUT:-$PROJECT_DIR/container.log}}"

echo "Getting logs from container: $CONTAINER"
docker logs -t "$CONTAINER" > "$OUTPUT" 2>&1

echo "Log saved to: $OUTPUT"
