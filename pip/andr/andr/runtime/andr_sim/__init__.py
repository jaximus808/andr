"""Bundled sim assets for `andr start --sim`.

This package ships the URDF, configs, default world, and a self-contained
launch file so the simulator can run from a pip wheel without needing the
colcon `andr_sim` package.

System dependencies (apt) are still required: gazebo_ros, nav2_bringup,
slam_toolbox, xacro, robot_state_publisher, rviz2.
"""

import os

ANDR_SIM_DIR = os.path.dirname(os.path.abspath(__file__))
DESCRIPTION_DIR = os.path.join(ANDR_SIM_DIR, "description")
CONFIG_DIR = os.path.join(ANDR_SIM_DIR, "config")
WORLDS_DIR = os.path.join(ANDR_SIM_DIR, "worlds")
LAUNCH_FILE = os.path.join(ANDR_SIM_DIR, "sim.launch.py")
DEFAULT_WORLD = os.path.join(WORLDS_DIR, "default.world")
