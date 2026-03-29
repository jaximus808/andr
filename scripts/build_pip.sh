#!/bin/bash
# build_pip.sh — Rebuild the andr pip package from the current colcon workspace.
#
# Run this after making changes to andr_msgs, BaseAgentTool, or BaseInputSource.
#
# Usage:
#   ./scripts/build_pip.sh          # just build
#   ./scripts/build_pip.sh publish   # build + upload to PyPI

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIP_DIR="${REPO_ROOT}/pip/andr"
INSTALL_DIR="${REPO_ROOT}/install"

# Detect Python version in the install space
PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
MSGS_SRC="${INSTALL_DIR}/andr_msgs/local/lib/python${PY_VERSION}/dist-packages/andr_msgs"
LIBS_SRC="${INSTALL_DIR}/andr_msgs/lib"

echo "=== ANDR pip package builder ==="
echo "  Repo:       ${REPO_ROOT}"
echo "  Python:     ${PY_VERSION}"
echo "  Msgs src:   ${MSGS_SRC}"
echo ""

# ── Step 1: Rebuild andr_msgs if needed ─────────────────────────────────
if [ ! -d "${MSGS_SRC}" ]; then
    echo ">>> andr_msgs not built. Building with colcon..."
    cd "${REPO_ROOT}"
    source /opt/ros/humble/setup.bash
    colcon build --packages-select andr_msgs
    source install/setup.bash
fi

# ── Step 1b: Clean stale egg-info ──────────────────────────────────────
rm -rf "${PIP_DIR}"/*.egg-info "${PIP_DIR}"/UNKNOWN.egg-info

# ── Step 2: Sync pre-generated andr_msgs bindings ──────────────────────
echo ">>> Syncing andr_msgs bindings..."
rm -rf "${PIP_DIR}/andr/_msgs_bind/andr_msgs"
mkdir -p "${PIP_DIR}/andr/_msgs_bind/andr_msgs"
cp -r "${MSGS_SRC}"/* "${PIP_DIR}/andr/_msgs_bind/andr_msgs/"

echo ">>> Syncing andr_msgs shared libraries..."
rm -rf "${PIP_DIR}/andr/_libs/"*.so
mkdir -p "${PIP_DIR}/andr/_libs"
cp "${LIBS_SRC}"/libandr_msgs*.so "${PIP_DIR}/andr/_libs/"
# Keep the __init__.py marker
touch "${PIP_DIR}/andr/_libs/__init__.py"

# ── Step 3: Sync base classes ───────────────────────────────────────────
echo ">>> Syncing base classes..."
cp "${REPO_ROOT}/andr_core/andr_tools/andr_tools/base_agent_tool.py" \
   "${PIP_DIR}/andr/tools/base_agent_tool.py"
cp "${REPO_ROOT}/andr_core/andr_tools/andr_tools/base_input_source.py" \
   "${PIP_DIR}/andr/tools/base_input_source.py"

# ── Step 3b: Sync runtime modules (agent, task_manager) ────────────────
echo ">>> Syncing runtime modules..."
rm -rf "${PIP_DIR}/andr/runtime/agent" "${PIP_DIR}/andr/runtime/task_manager"
mkdir -p "${PIP_DIR}/andr/runtime"
cp -r "${REPO_ROOT}/andr_core/agent/agent" "${PIP_DIR}/andr/runtime/agent"
cp -r "${REPO_ROOT}/andr_core/task_manager/task_manager" "${PIP_DIR}/andr/runtime/task_manager"
# Clean pycache
find "${PIP_DIR}/andr/runtime" -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
rm -rf "${PIP_DIR}/andr/runtime/andr_ui"
cp -r "${REPO_ROOT}/andr_ui/andr_ui" "${PIP_DIR}/andr/runtime/andr_ui"
find "${PIP_DIR}/andr/runtime" -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
touch "${PIP_DIR}/andr/runtime/__init__.py"

# ── Step 3c: Sync tool_manager binary ──────────────────────────────────
echo ">>> Syncing tool_manager binary..."
mkdir -p "${PIP_DIR}/andr/bin"
TM_BIN="${INSTALL_DIR}/tool_manager/lib/tool_manager/tool_manager_node"
if [ -f "${TM_BIN}" ]; then
    cp "${TM_BIN}" "${PIP_DIR}/andr/bin/tool_manager_node"
    chmod +x "${PIP_DIR}/andr/bin/tool_manager_node"
else
    echo "  WARNING: tool_manager_node not found at ${TM_BIN}"
    echo "  Run: colcon build --packages-select tool_manager"
fi

# ── Step 4: Build the wheel ────────────────────────────────────────────
echo ">>> Building wheel..."
cd "${PIP_DIR}"
rm -rf dist/ build/ *.egg-info
mkdir -p dist

if python3 -c "import build" 2>/dev/null; then
    python3 -m build --wheel --outdir dist/ 2>&1 | tail -5
else
    echo "  ('build' module not installed, using pip wheel instead)"
    pip3 wheel --no-deps --wheel-dir dist/ . 2>&1 | tail -5
fi

echo ""
echo ">>> Built:"
ls -lh dist/

# ── Step 5: Publish (optional) ─────────────────────────────────────────
if [ "$1" = "publish" ]; then
    echo ""
    echo ">>> Publishing to PyPI..."
    python3 -m twine upload dist/*
    echo ">>> Published!"
elif [ "$1" = "test-publish" ]; then
    echo ""
    echo ">>> Publishing to TestPyPI..."
    python3 -m twine upload --repository testpypi dist/*
    echo ">>> Published to TestPyPI!"
    echo ">>> Install with: pip install --index-url https://test.pypi.org/simple/ andr"
else
    echo ""
    echo "To publish:      ./scripts/build_pip.sh publish"
    echo "To test publish: ./scripts/build_pip.sh test-publish"
    echo "To install locally: pip install -e ${PIP_DIR}"
fi
