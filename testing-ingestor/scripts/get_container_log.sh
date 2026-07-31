#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

CONTAINER="${1:-playground-codex-test-emr-integration-data-parsing-worker_DEV-1}"
OUTPUT="${2:-$PROJECT_DIR/container.log}"

echo "Getting logs from container: $CONTAINER"
docker logs -t "$CONTAINER" > "$OUTPUT" 2>&1

echo "Log saved to: $OUTPUT"
