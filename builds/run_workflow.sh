#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

IMAGE="${1:-maw-sandbox:latest}"

echo "=== Starting workflow: $IMAGE ==="
echo "  SCRIPT_DIR: $SCRIPT_DIR"
echo "  REPO_DIR:   $REPO_DIR"

docker run --rm \
  -v "$REPO_DIR":/app \
  -w /app/builds \
  "$IMAGE" \
  python3 /app/builds/workflow.py \
  --data-dir /app/data \
  --work-dir /app/work/run0
