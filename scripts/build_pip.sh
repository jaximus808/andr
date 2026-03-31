#!/bin/bash
# build_pip.sh — Build the andr pip wheel for distribution.
#
# Python source files (tools, runtime) are symlinked to the real source
# directories — setuptools follows them automatically during wheel build.
# This script only handles compiled artifacts:
#   - andr_msgs .so libraries + Python bindings
#   - tool_manager_node binary
#
# Usage:
#   ./scripts/build_pip.sh              # just build wheel
#   ./scripts/build_pip.sh publish      # build + upload to PyPI
#   ./scripts/build_pip.sh test-publish # build + upload to TestPyPI

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIP_DIR="${REPO_ROOT}/pip/andr"
INSTALL_DIR="${REPO_ROOT}/install"

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
MSGS_SRC="${INSTALL_DIR}/andr_msgs/local/lib/python${PY_VERSION}/dist-packages/andr_msgs"
LIBS_SRC="${INSTALL_DIR}/andr_msgs/lib"

echo "=== ANDR pip package builder ==="
echo "  Repo:       ${REPO_ROOT}"
echo "  Python:     ${PY_VERSION}"
echo ""

# ── Step 1: Build andr_msgs + tool_manager if needed ───────────────────
NEED_BUILD=()

if [ ! -d "${MSGS_SRC}" ]; then
    NEED_BUILD+=(andr_msgs)
fi

# ── Step 1b: Clean stale egg-info ──────────────────────────────────────
rm -rf "${PIP_DIR}"/*.egg-info "${PIP_DIR}"/UNKNOWN.egg-info

# ── Step 2: Sync pre-generated andr_msgs bindings ──────────────────────
TM_BIN="${INSTALL_DIR}/tool_manager/lib/tool_manager/tool_manager_node"
if [ ! -f "${TM_BIN}" ]; then
    NEED_BUILD+=(tool_manager)
fi

if [ ${#NEED_BUILD[@]} -gt 0 ]; then
    echo ">>> Building: ${NEED_BUILD[*]}..."
    cd "${REPO_ROOT}"
    source /opt/ros/humble/setup.bash
    colcon build --packages-select "${NEED_BUILD[@]}"
    source install/setup.bash
    echo ""
fi

# ── Step 2: Sync compiled artifacts ────────────────────────────────────
echo ">>> Syncing andr_msgs bindings..."
rm -rf "${PIP_DIR}/andr/_msgs_bind/andr_msgs"
mkdir -p "${PIP_DIR}/andr/_msgs_bind/andr_msgs"
cp -r "${MSGS_SRC}"/* "${PIP_DIR}/andr/_msgs_bind/andr_msgs/"
touch "${PIP_DIR}/andr/_msgs_bind/__init__.py"

echo ">>> Syncing andr_msgs shared libraries..."
rm -f "${PIP_DIR}/andr/_libs/"*.so
mkdir -p "${PIP_DIR}/andr/_libs"
cp "${LIBS_SRC}"/libandr_msgs*.so "${PIP_DIR}/andr/_libs/"
touch "${PIP_DIR}/andr/_libs/__init__.py"

echo ">>> Syncing tool_manager binary..."
mkdir -p "${PIP_DIR}/andr/bin"
if [ -f "${TM_BIN}" ]; then
    cp "${TM_BIN}" "${PIP_DIR}/andr/bin/tool_manager_node"
    chmod +x "${PIP_DIR}/andr/bin/tool_manager_node"
else
    echo "  WARNING: tool_manager_node not found at ${TM_BIN}"
    echo "  Run: colcon build --packages-select tool_manager"
fi

# ── Step 3: Build the wheel ────────────────────────────────────────────
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

# ── Step 4: Publish (optional) ─────────────────────────────────────────
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
    echo "For development: pip install -e ${PIP_DIR}"
    echo "To publish:      ./scripts/build_pip.sh publish"
    echo "To test publish: ./scripts/build_pip.sh test-publish"
fi
