"""Launch file for the real ANDR robot (no Gazebo).

Starts:
  1. Robot State Publisher  — URDF → TF (joint transforms)
  2. EKF (robot_localization) — fuses wheel odom + IMU → /odometry/filtered
  3. SLAM Toolbox            — mapping or localization mode
  4. Map server (ANDR)       — manages saved maps / points
  5. Nav2                    — full navigation stack

Sensors (must be running separately or via micro-ROS agent):
  - /wheel/odom   (nav_msgs/Odometry)  — wheel encoder odometry
  - /imu/data     (sensor_msgs/Imu)    — IMU
  - /scan         (sensor_msgs/LaserScan) — LiDAR
"""

import json
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def _load_slam_config():
    """Read persisted SLAM config from ~/andr_maps/slam_config.json."""
    config_path = os.path.expanduser("~/andr_maps/slam_config.json")
    try:
        with open(config_path) as f:
            cfg = json.load(f)
        map_file = cfg.get("map_file", "") or ""
        localization = cfg.get("localization", False)
        return map_file, "true" if localization else "false"
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return "", "false"


def generate_launch_description():
    pkg_share = get_package_share_directory("andr_sim")
    nav2_bringup_share = get_package_share_directory("nav2_bringup")

    _cfg_map_file, _cfg_localization = _load_slam_config()

    localization = LaunchConfiguration("localization")
    map_file = LaunchConfiguration("map_file")
    launch_nav2 = LaunchConfiguration("launch_nav2")

    # ── Robot State Publisher ────────────────────────────────────────────
    rsp = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_share, "launch", "rsp.launch.py")
        ),
        launch_arguments={"use_sim_time": "false"}.items(),
    )

    # ── EKF — sensor fusion (wheel odom + IMU) ──────────────────────────
    ekf_config = os.path.join(pkg_share, "config", "ekf.yaml")

    ekf = Node(
        package="robot_localization",
        executable="ekf_node",
        name="ekf_filter_node",
        output="screen",
        parameters=[ekf_config],
    )

    # ── SLAM Toolbox: mapping mode (default) ────────────────────────────
    slam_mapping_params = os.path.join(
        pkg_share, "config", "slam_toolbox_params.yaml"
    )

    slam_mapping = Node(
        package="slam_toolbox",
        executable="async_slam_toolbox_node",
        name="slam_toolbox",
        output="screen",
        parameters=[slam_mapping_params, {"use_sim_time": False}],
        condition=UnlessCondition(localization),
    )

    # ── SLAM Toolbox: localization mode ─────────────────────────────────
    slam_localization_params = os.path.join(
        pkg_share, "config", "slam_toolbox_localization_params.yaml"
    )

    slam_localization = Node(
        package="slam_toolbox",
        executable="localization_slam_toolbox_node",
        name="slam_toolbox",
        output="screen",
        parameters=[
            slam_localization_params,
            {
                "use_sim_time": False,
                "map_file_name": map_file,
            },
        ],
        condition=IfCondition(localization),
    )

    # ── Map manager node (ANDR custom) ──────────────────────────────────
    map_server_node = Node(
        package="andr_nav",
        executable="map_server",
        name="map_server",
        output="screen",
        parameters=[{
            "use_sim_time": False,
            "slam_params_mapping": os.path.join(
                pkg_share, "config", "slam_toolbox_params.yaml"
            ),
            "slam_params_localization": os.path.join(
                pkg_share, "config", "slam_toolbox_localization_params.yaml"
            ),
        }],
    )

    # ── Nav2 navigation stack ───────────────────────────────────────────
    nav2_params = os.path.join(pkg_share, "config", "nav2_params.yaml")

    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(nav2_bringup_share, "launch", "navigation_launch.py")
        ),
        launch_arguments={
            "use_sim_time": "False",
            "params_file": nav2_params,
        }.items(),
        condition=IfCondition(launch_nav2),
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "localization", default_value=_cfg_localization,
            description="Run SLAM in localization mode instead of mapping",
        ),
        DeclareLaunchArgument(
            "map_file", default_value=_cfg_map_file,
            description="Serialized pose graph path (no extension) for localization mode",
        ),
        DeclareLaunchArgument(
            "launch_nav2", default_value="true",
            description="Launch the Nav2 navigation stack",
        ),

        rsp,
        ekf,
        slam_mapping,
        slam_localization,
        map_server_node,
        nav2,
    ])
