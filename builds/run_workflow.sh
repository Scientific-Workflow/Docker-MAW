#!/usr/bin/env bash
# ===========================================================================
# run_workflow.sh — Launch the LAMMPS+OVITO Parsl workflow inside Docker
# ===========================================================================
set -euo pipefail

# Resolve paths relative to this script, never from $(pwd)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Docker image tag (first argument or default)
IMAGE="${1:-maw-sandbox:latest}"

echo "=================================================================="
echo "Water Freezing MD Workflow — Docker Launcher"
echo "=================================================================="
echo "  Image     : $IMAGE"
echo "  Repo root : $REPO_DIR"
echo "  Script dir: $SCRIPT_DIR"
echo "  Workflow  : /app/builds/workflow.py"
echo "=================================================================="
echo ""

docker run --rm \
    -v "$REPO_DIR":/app \
    -w /app/builds \
    "$IMAGE" \
    python3 /app/builds/workflow.py \
        --data-dir /app/data \
        --work-dir /app/work/run0
