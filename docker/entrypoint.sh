#!/bin/bash
# ANDR Docker entrypoint — uses the `andr` CLI to launch the stack.
#
# Modes:
#   full   — Full stack: tool_manager + agent + task_manager + UI (default)
#   core   — Core only: tool_manager + agent + task_manager, no UI
#   bash   — Drop into a shell
#
# Environment variables:
#   ANDR_LLM_BACKEND     — ollama | openai (default: ollama)
#   ANDR_LLM_MODEL       — model name (default: llama3.2)
#   ANDR_LLM_HOST        — Ollama URL (default: http://localhost:11434)
#   ANDR_LLM_TEMPERATURE — sampling temp (default: 0.2)
#   ANDR_UI_PORT         — web UI port (default: 8080)
#   ANDR_MAX_ITERATIONS  — agent ReAct loop cap (default: 20)
#   ANDR_TOOLS           — comma-separated tools to launch (e.g., speak,walk)
#   OPENAI_API_KEY       — required if using openai backend

set -e

# Source ROS 2
source /opt/ros/humble/setup.bash

MODE="${1:-full}"

# Build CLI args from environment variables
CLI_ARGS=""
[ -n "$ANDR_LLM_BACKEND" ]     && CLI_ARGS="$CLI_ARGS --backend $ANDR_LLM_BACKEND"
[ -n "$ANDR_LLM_MODEL" ]       && CLI_ARGS="$CLI_ARGS --model $ANDR_LLM_MODEL"
[ -n "$ANDR_LLM_HOST" ]        && CLI_ARGS="$CLI_ARGS --host $ANDR_LLM_HOST"
[ -n "$ANDR_LLM_TEMPERATURE" ] && CLI_ARGS="$CLI_ARGS --temperature $ANDR_LLM_TEMPERATURE"
[ -n "$ANDR_MAX_ITERATIONS" ]   && CLI_ARGS="$CLI_ARGS --max-iterations $ANDR_MAX_ITERATIONS"
[ -n "$ANDR_UI_PORT" ]          && CLI_ARGS="$CLI_ARGS --ui-port $ANDR_UI_PORT"
[ -n "$ANDR_TOOLS" ]            && CLI_ARGS="$CLI_ARGS --tools $ANDR_TOOLS"

case "$MODE" in
  full)
    echo "=== ANDR: Starting full stack ==="
    echo "  Backend: ${ANDR_LLM_BACKEND:-ollama}"
    echo "  Model:   ${ANDR_LLM_MODEL:-llama3.2}"
    echo "  Host:    ${ANDR_LLM_HOST:-http://localhost:11434}"
    echo "  UI Port: ${ANDR_UI_PORT:-8080}"
    echo ""
    exec andr start $CLI_ARGS
    ;;

  core)
    echo "=== ANDR: Starting core (no UI) ==="
    exec andr start --no-ui $CLI_ARGS
    ;;

  bash)
    exec /bin/bash
    ;;

  *)
    echo "Unknown mode: $MODE"
    echo "Usage: docker run andr [full|core|bash]"
    exit 1
    ;;
esac
