#!/usr/bin/env bash
# Sync runtime Python files from andr_core source into the pip package.
# Run this before `pip install` or `pip install -e .` to pick up changes.
#
# Usage:  ./sync_runtime.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUNTIME_DIR="$(cd "$(dirname "$0")" && pwd)/andr/runtime"

echo "Syncing andr_core → pip runtime..."

# Agent
rsync -av --delete \
    "$REPO_ROOT/andr_core/agent/agent/" \
    "$RUNTIME_DIR/agent/" \
    --exclude '__pycache__'

# Task manager
rsync -av --delete \
    "$REPO_ROOT/andr_core/task_manager/task_manager/" \
    "$RUNTIME_DIR/task_manager/" \
    --exclude '__pycache__'

echo "Done."
