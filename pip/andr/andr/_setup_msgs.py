"""_setup_msgs.py — Make bundled andr_msgs importable at runtime.

Search order for .so libraries and Python bindings:
  1. ANDR_WORKSPACE env var → colcon install/ directory
  2. Auto-detected repo root (development mode) → colcon install/
  3. Already importable (user sourced a colcon workspace) → skip
  4. Bundled _libs/ and _msgs_bind/ (pip wheel install) → fallback

Requirements:
  - ros-humble-ros-base must be installed (provides rclpy, rosidl runtime libs)
  - No need to source /opt/ros/humble/setup.bash — we detect it automatically
"""

import ctypes
import glob
import os
import sys
import warnings


def _find_ros_lib_dirs():
    """Find ROS 2 library directories needed by andr_msgs .so files.

    Returns a list of directories containing ROS runtime libraries.
    """
    dirs = []

    # 1. Check AMENT_PREFIX_PATH (set when a workspace is sourced)
    ament = os.environ.get("AMENT_PREFIX_PATH", "")
    if ament:
        for prefix in ament.split(":"):
            lib_dir = os.path.join(prefix, "lib")
            if os.path.isdir(lib_dir):
                dirs.append(lib_dir)

    # 2. Check common ROS install locations
    for distro in ("humble", "iron", "jazzy", "rolling"):
        ros_lib = f"/opt/ros/{distro}/lib"
        if os.path.isdir(ros_lib) and ros_lib not in dirs:
            dirs.append(ros_lib)
            break

    return dirs


def _find_repo_root():
    """Walk up from this file to find the repo root (contains .git/).

    Returns the repo root path, or None if not found (e.g. pip wheel install).
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def _ensure_ld_path(*dirs):
    """Add directories to LD_LIBRARY_PATH if not already present."""
    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    parts = ld_path.split(":") if ld_path else []
    added = []
    for d in dirs:
        if d and d not in parts:
            added.append(d)
    if added:
        os.environ["LD_LIBRARY_PATH"] = ":".join(added + parts)


def _preload_so_files(directory):
    """Pre-load all .so files from a directory using ctypes.

    Uses RTLD_GLOBAL so symbols are available to subsequent loads.
    """
    if not os.path.isdir(directory):
        return
    for so_file in sorted(os.listdir(directory)):
        if so_file.endswith(".so"):
            try:
                ctypes.CDLL(
                    os.path.join(directory, so_file),
                    mode=ctypes.RTLD_GLOBAL,
                )
            except OSError as e:
                warnings.warn(
                    f"andr: failed to pre-load {so_file}: {e}",
                    stacklevel=3,
                )


def _try_colcon_install(install_base):
    """Load .so files and add msg bindings from a colcon install directory.

    Returns True if andr_msgs bindings were found and added to sys.path.
    """
    msgs_pkg = os.path.join(install_base, "andr_msgs")
    if not os.path.isdir(msgs_pkg):
        return False

    # Find .so files
    libs_dir = os.path.join(msgs_pkg, "lib")
    so_files = glob.glob(os.path.join(libs_dir, "libandr_msgs*.so"))
    if not so_files:
        return False

    # Add ROS libs + colcon libs to LD_LIBRARY_PATH, then pre-load
    ros_dirs = _find_ros_lib_dirs()
    _ensure_ld_path(libs_dir, *ros_dirs)
    _preload_so_files(libs_dir)

    # Find Python bindings (python3.X path varies)
    bindings = glob.glob(
        os.path.join(
            msgs_pkg, "local", "lib", "python3.*", "dist-packages"
        )
    )
    if bindings and os.path.isdir(os.path.join(bindings[0], "andr_msgs")):
        if bindings[0] not in sys.path:
            sys.path.insert(0, bindings[0])
        return True

    return False


def _try_bundled():
    """Load from bundled _libs/ and _msgs_bind/ (pip wheel install).

    Returns True if bundled files were found and loaded.
    """
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    libs_dir = os.path.join(pkg_dir, "_libs")
    msgs_dir = os.path.join(pkg_dir, "_msgs_bind")

    if not os.path.isdir(libs_dir):
        return False

    so_files = [f for f in os.listdir(libs_dir) if f.endswith(".so")]
    if not so_files:
        return False

    # Add ROS libs + bundled libs to LD_LIBRARY_PATH, then pre-load
    ros_dirs = _find_ros_lib_dirs()
    _ensure_ld_path(libs_dir, *ros_dirs)
    _preload_so_files(libs_dir)

    # Add msgs bindings to sys.path
    if os.path.isdir(msgs_dir) and msgs_dir not in sys.path:
        sys.path.insert(0, msgs_dir)

    return True


def setup():
    """Patch sys.path and LD_LIBRARY_PATH so andr_msgs is importable.

    Called once at ``import andr`` time.
    """
    # 1. Explicit workspace override
    ws = os.environ.get("ANDR_WORKSPACE")
    if ws:
        install_base = os.path.join(ws, "install")
        if _try_colcon_install(install_base):
            return

    # 2. Auto-detect repo root → colcon install (development mode)
    repo_root = _find_repo_root()
    if repo_root:
        install_base = os.path.join(repo_root, "install")
        if _try_colcon_install(install_base):
            return

    # 3. Already importable (user sourced a workspace)?
    try:
        import andr_msgs  # noqa: F401
        return
    except ImportError:
        pass

    # 4. Bundled fallback (pip wheel)
    if _try_bundled():
        return

    # If nothing worked, andr_msgs will fail at actual import time
    # with a clear ImportError — no silent suppression.
