"""Bundled sim launch file for `andr start --sim`.

Self-contained variant of `andr_sim/launch/robot.launch.py` for the pip wheel.
Resolves URDF/configs/RViz paths relative to its own location instead of via
`get_package_share_directory("andr_sim")`, since the colcon andr_sim package
isn't installed when running from pip.

Still uses `get_package_share_directory` for `gazebo_ros` and `nav2_bringup`,
which come from apt.

Differences from the colcon version:
  - Paths resolved via __file__, not ament index
  - No andr_nav/map_server (TODO: bundle map_server when pip ships andr_nav)
  - No SLAM config JSON read (depends on map_server)
  - Adds `launch_rviz` argument so the CLI's --no-rviz flag works
"""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DESCRIPTION_DIR = os.path.join(_THIS_DIR, "description")
_CONFIG_DIR = os.path.join(_THIS_DIR, "config")
_WORLDS_DIR = os.path.join(_THIS_DIR, "worlds")


def generate_launch_description():
    gazebo_ros_share = get_package_share_directory("gazebo_ros")
    nav2_bringup_share = get_package_share_directory("nav2_bringup")

    world_file   = LaunchConfiguration("world")
    use_sim_time = LaunchConfiguration("use_sim_time")
    launch_nav2  = LaunchConfiguration("launch_nav2")
    launch_rviz  = LaunchConfiguration("launch_rviz")

    # ── Robot State Publisher ─────────────────────────────────────────────
    xacro_file = os.path.join(_DESCRIPTION_DIR, "robot.urdf.xacro")
    robot_description = Command(["xacro ", xacro_file])

    rsp = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        parameters=[{
            "robot_description": robot_description,
            "use_sim_time": use_sim_time,
        }],
        output="screen",
    )

    # ── Gazebo server + client ────────────────────────────────────────────
    gazebo_params = os.path.join(_CONFIG_DIR, "gazebo_params.yaml")

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(gazebo_ros_share, "launch", "gazebo.launch.py")
        ),
        launch_arguments={
            "world": world_file,
            "extra_gazebo_args": "--ros-args --params-file " + gazebo_params,
        }.items(),
    )

    # ── Spawn the robot ───────────────────────────────────────────────────
    spawn_entity = Node(
        package="gazebo_ros",
        executable="spawn_entity.py",
        arguments=[
            "-topic", "robot_description",
            "-entity", "andr",
            "-x", "0.0",
            "-y", "0.0",
            "-z", "0.1",
        ],
        output="screen",
    )

    # ── RViz ─────────────────────────────────────────────────────────────
    rviz_config = os.path.join(_CONFIG_DIR, "sim.rviz")

    rviz = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="screen",
        arguments=["-d", rviz_config],
        parameters=[{"use_sim_time": True}],
        condition=IfCondition(launch_rviz),
    )

    # ── SLAM Toolbox: mapping mode ────────────────────────────────────────
    # TODO: bundle map_server so localization mode + map saving work from pip.
    # For v1 we run mapping mode only — sufficient for the basic demo loop.
    slam_mapping_params = os.path.join(_CONFIG_DIR, "slam_toolbox_params.yaml")

    slam_mapping = Node(
        package="slam_toolbox",
        executable="async_slam_toolbox_node",
        name="slam_toolbox",
        output="screen",
        parameters=[slam_mapping_params, {"use_sim_time": True}],
    )

    # ── Nav2 navigation stack ─────────────────────────────────────────────
    nav2_params = os.path.join(_CONFIG_DIR, "nav2_params.yaml")

    nav2 = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(nav2_bringup_share, "launch", "navigation_launch.py")
        ),
        launch_arguments={
            "use_sim_time": "True",
            "params_file": nav2_params,
        }.items(),
        condition=IfCondition(launch_nav2),
    )

    return LaunchDescription([
        DeclareLaunchArgument(
            "use_sim_time", default_value="true",
            description="Use simulation clock",
        ),
        DeclareLaunchArgument(
            "world",
            default_value=os.path.join(_WORLDS_DIR, "default.world"),
            description="Path to Gazebo world file",
        ),
        DeclareLaunchArgument(
            "launch_nav2", default_value="true",
            description="Launch the Nav2 navigation stack",
        ),
        DeclareLaunchArgument(
            "launch_rviz", default_value="true",
            description="Launch RViz",
        ),

        rsp,
        gazebo,
        spawn_entity,
        slam_mapping,
        nav2,
        rviz,
    ])
