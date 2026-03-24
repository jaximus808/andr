#!/bin/bash
# =============================================================================
# start.sh — ANDR robot startup script for Jetson
# =============================================================================
# Run this on boot (via systemd or rc.local) or manually via SSH.
#
# What it does, in order:
#   1. Pull latest code from git
#   2. Check if ESP32 firmware changed — flash if needed
#   3. Start micro-ROS agent (talks to ESP32 over serial)
#   4. Build the ROS workspace (if needed)
#   5. Launch robot hardware stack (EKF, SLAM, Nav2)
#   6. Launch tools (tool_manager + all skill servers)
#   7. Launch agent (brain, LLM agent, task_manager, UI)
#
# Usage:
#   ./scripts/start.sh                  # full startup
#   ./scripts/start.sh --no-flash       # skip firmware check
#   ./scripts/start.sh --no-pull        # skip git pull
#   ./scripts/start.sh --no-build       # skip colcon build
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Configuration ───────────────────────────────────────────────────────────
ESP32_PORT="${ESP32_PORT:-/dev/ttyUSB0}"
ESP32_BAUD="${ESP32_BAUD:-921600}"
FIRMWARE_DIR="${REPO_ROOT}/firmware/esp32"
FIRMWARE_HASH_FILE="${REPO_ROOT}/.firmware_hash"
GIT_BRANCH="${GIT_BRANCH:-main}"
MICRO_ROS_AGENT_BAUD="${MICRO_ROS_AGENT_BAUD:-921600}"

# Launch options (override via env vars)
LAUNCH_VISION="${LAUNCH_VISION:-false}"
LLM_MODEL="${LLM_MODEL:-qwen2.5:3b}"
LLM_BACKEND="${LLM_BACKEND:-ollama}"
UI_PORT="${UI_PORT:-8080}"

# ── Parse flags ─────────────────────────────────────────────────────────────
DO_PULL=true
DO_FLASH=true
DO_BUILD=true

for arg in "$@"; do
    case "$arg" in
        --no-pull)  DO_PULL=false ;;
        --no-flash) DO_FLASH=false ;;
        --no-build) DO_BUILD=false ;;
        --help|-h)
            head -25 "$0" | tail -18
            exit 0
            ;;
    esac
done

# ── Helpers ─────────────────────────────────────────────────────────────────
log()  { echo -e "\n\033[1;36m[$1]\033[0m $2"; }
err()  { echo -e "\n\033[1;31m[ERROR]\033[0m $1" >&2; }
ok()   { echo -e "  \033[1;32m✓\033[0m $1"; }
skip() { echo -e "  \033[1;33m–\033[0m $1 (skipped)"; }

cleanup() {
    log "SHUTDOWN" "Stopping all ANDR processes..."
    # Kill backgrounded micro-ROS agent
    if [ -n "${UROS_PID:-}" ] && kill -0 "$UROS_PID" 2>/dev/null; then
        kill "$UROS_PID" 2>/dev/null || true
        wait "$UROS_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# =============================================================================
# 1. Pull latest code
# =============================================================================
log "1/7" "Pulling latest code..."
if $DO_PULL; then
    cd "$REPO_ROOT"
    git fetch origin "$GIT_BRANCH" && git pull origin "$GIT_BRANCH"
    ok "Updated to $(git rev-parse --short HEAD)"
else
    skip "git pull"
fi

# =============================================================================
# 2. Check ESP32 firmware & flash if changed
# =============================================================================
log "2/7" "Checking ESP32 firmware..."
if $DO_FLASH; then
    if [ ! -d "$FIRMWARE_DIR" ]; then
        skip "No firmware directory at $FIRMWARE_DIR"
    else
        # Hash all firmware source files to detect changes
        CURRENT_HASH=$(find "$FIRMWARE_DIR" -type f \( -name '*.ino' -o -name '*.cpp' -o -name '*.h' -o -name '*.c' \) \
            -exec sha256sum {} \; | sort | sha256sum | cut -d' ' -f1)
        PREVIOUS_HASH=""
        if [ -f "$FIRMWARE_HASH_FILE" ]; then
            PREVIOUS_HASH=$(cat "$FIRMWARE_HASH_FILE")
        fi

        if [ "$CURRENT_HASH" = "$PREVIOUS_HASH" ]; then
            ok "Firmware unchanged — no flash needed"
        else
            log "FLASH" "Firmware changed, flashing ESP32 on $ESP32_PORT..."

            # Wait for serial port
            for i in $(seq 1 10); do
                [ -e "$ESP32_PORT" ] && break
                echo "  Waiting for $ESP32_PORT... ($i/10)"
                sleep 1
            done

            if [ ! -e "$ESP32_PORT" ]; then
                err "ESP32 not found at $ESP32_PORT — skipping flash"
            else
                # Flash using arduino-cli (install separately)
                # Adjust board FQBN for your ESP32 variant
                arduino-cli compile --fqbn esp32:esp32:esp32 "$FIRMWARE_DIR" \
                    && arduino-cli upload --fqbn esp32:esp32:esp32 --port "$ESP32_PORT" "$FIRMWARE_DIR"

                echo "$CURRENT_HASH" > "$FIRMWARE_HASH_FILE"
                ok "ESP32 flashed successfully"

                # Give ESP32 time to reboot after flash
                sleep 3
            fi
        fi
    fi
else
    skip "firmware check"
fi

# =============================================================================
# 3. Start micro-ROS agent
# =============================================================================
log "3/7" "Starting micro-ROS agent on $ESP32_PORT..."

# Wait for serial port to be available
for i in $(seq 1 10); do
    [ -e "$ESP32_PORT" ] && break
    echo "  Waiting for $ESP32_PORT... ($i/10)"
    sleep 1
done

if [ ! -e "$ESP32_PORT" ]; then
    err "ESP32 not found at $ESP32_PORT — micro-ROS agent not started"
    err "Wheel odom and IMU topics will not be available"
    UROS_PID=""
else
    ros2 run micro_ros_agent micro_ros_agent serial \
        --dev "$ESP32_PORT" \
        --baud "$MICRO_ROS_AGENT_BAUD" &
    UROS_PID=$!
    ok "micro-ROS agent started (PID $UROS_PID)"

    # Give the agent a moment to connect and publish topics
    sleep 2
fi

# =============================================================================
# 4. Build workspace (if needed)
# =============================================================================
log "4/7" "Building ROS workspace..."
if $DO_BUILD; then
    cd "$REPO_ROOT"
    source /opt/ros/humble/setup.bash
    colcon build --symlink-install 2>&1 | tail -5
    ok "Build complete"
else
    skip "colcon build"
fi

# Source the workspace
source /opt/ros/humble/setup.bash
source "$REPO_ROOT/install/setup.bash"

# =============================================================================
# 5. Launch robot hardware stack (EKF + SLAM + Nav2)
# =============================================================================
log "5/7" "Launching robot hardware stack (EKF + SLAM + Nav2)..."
ros2 launch andr_sim robot_real.launch.py &
ROBOT_PID=$!
ok "robot_real.launch.py started (PID $ROBOT_PID)"

# Give hardware stack time to initialize before starting tools
sleep 5

# =============================================================================
# 6. Launch tools
# =============================================================================
log "6/7" "Launching tool servers..."
ros2 launch andr_launch tools.launch.py \
    launch_vision:="$LAUNCH_VISION" &
TOOLS_PID=$!
ok "tools.launch.py started (PID $TOOLS_PID)"

# Give tools time to register with tool_manager
sleep 3

# =============================================================================
# 7. Launch agent stack
# =============================================================================
log "7/7" "Launching agent stack..."
ros2 launch andr_launch andr.launch.py \
    llm_model:="$LLM_MODEL" \
    llm_backend:="$LLM_BACKEND" \
    ui_port:="$UI_PORT" &
AGENT_PID=$!
ok "andr.launch.py started (PID $AGENT_PID)"

# =============================================================================
# Ready
# =============================================================================
echo ""
echo "======================================================"
echo "  ANDR robot is running"
echo "  UI:       http://localhost:${UI_PORT}"
echo "  LLM:      ${LLM_BACKEND} / ${LLM_MODEL}"
echo "  ESP32:    ${ESP32_PORT}"
echo "  Vision:   ${LAUNCH_VISION}"
echo "======================================================"
echo "  Press Ctrl+C to stop everything"
echo "======================================================"

# Wait for any child to exit — if one dies, the trap will clean up
wait
