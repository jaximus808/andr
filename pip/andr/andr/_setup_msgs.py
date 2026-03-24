"""_setup_msgs.py — Make bundled andr_msgs importable at runtime.

This module does two things:
  1. Adds the bundled ``_libs/`` directory to LD_LIBRARY_PATH so the
     platform-specific .so files inside ``_msgs_bind/andr_msgs/`` can
     find ``libandr_msgs__rosidl_*.so`` at import time.
  2. Adds ``_msgs_bind/`` to ``sys.path`` so ``import andr_msgs`` resolves
     to the pre-generated bindings shipped with this package.

This is called once at ``import andr`` time. The user never needs to
source a colcon workspace or set environment variables manually.

Requirements:
  - ros-humble-ros-base must be installed (provides rclpy, rosidl, etc.)
  - /opt/ros/humble/setup.bash should be sourced (or equivalent env vars set)
"""

import ctypes
import os
import sys


def setup():
    """Patch sys.path and LD_LIBRARY_PATH so andr_msgs is importable."""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    libs_dir = os.path.join(pkg_dir, "_libs")
    msgs_dir = os.path.join(pkg_dir, "_msgs_bind")

    # 1. Add our bundled .so libs to the loader search path
    #    This must happen BEFORE any import of andr_msgs
    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    if libs_dir not in ld_path:
        os.environ["LD_LIBRARY_PATH"] = libs_dir + ":" + ld_path

    # Also use ctypes to pre-load them (LD_LIBRARY_PATH changes after
    # process start don't always take effect on all platforms)
    for so_file in sorted(os.listdir(libs_dir)):
        if so_file.endswith(".so"):
            try:
                ctypes.CDLL(os.path.join(libs_dir, so_file))
            except OSError:
                pass  # Will fail at actual import time with a better error

    # 2. Add the msgs bind directory to sys.path so `import andr_msgs` works
    if msgs_dir not in sys.path:
        sys.path.insert(0, msgs_dir)
