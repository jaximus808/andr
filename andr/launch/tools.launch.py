"""
tools.launch.py — Launch the tool manager and all tool action servers.

Start this BEFORE the main andr.launch.py so that tools are registered
and ready when the agent comes up.

Usage
-----
ros2 launch andr tools.launch.py
ros2 launch andr tools.launch.py log_level:=debug
ros2 launch andr tools.launch.py launch_vision:=true vlm_model:=llava

Launch arguments
----------------
log_level       string  info      ROS log level (debug/info/warn/error).
launch_vision   bool    false     Start the vision + describe_scene servers.
vlm_backend     string  ollama    VLM backend: ollama | openai.
vlm_model       string  llava     Vision-language model name.
vlm_host        string  http://localhost:11434  Ollama server URL.
"""

from __future__ import annotations

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:

    args = [
        DeclareLaunchArgument(
            "log_level",
            default_value="info",
            choices=["debug", "info", "warn", "error"],
            description="ROS log level for all nodes",
        ),
        DeclareLaunchArgument(
            "launch_vision", default_value="false",
            description="Start the vision + describe_scene + gesture servers",
        ),
        DeclareLaunchArgument(
            "vlm_backend", default_value="ollama",
            description="VLM backend: ollama | openai",
        ),
        DeclareLaunchArgument(
            "vlm_model", default_value="llava",
            description="Vision-language model name",
        ),
        DeclareLaunchArgument(
            "vlm_host", default_value="http://localhost:11434",
            description="Ollama server URL",
        ),
    ]

    log_level = LaunchConfiguration("log_level")
    ros_args = ["--ros-args", "--log-level", log_level]

    # ── Tool manager (C++) ───────────────────────────────────────────────
    tool_manager_node = Node(
        package="tool_manager",
        executable="tool_manager_node",
        name="tool_manager",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
    )

    # ── Tool action servers (Python, robot_skills) ───────────────────────
    speak_server_node = Node(
        package="robot_skills",
        executable="speak_server",
        name="speak_server",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
    )

    walk_server_node = Node(
        package="robot_skills",
        executable="walk_server",
        name="walk_server",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
    )

    spin_server_node = Node(
        package="robot_skills",
        executable="spin_server",
        name="spin_server",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
    )

    navigate_to_point_server_node = Node(
        package="robot_skills",
        executable="navigate_to_point_server",
        name="navigate_to_point_server",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
    )

    go_to_point_server_node = Node(
        package="robot_skills",
        executable="go_to_point_server",
        name="go_to_point_server",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
    )

    map_server_node = Node(
        package="robot_skills",
        executable="map_server",
        name="map_server",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
    )

    # ── Vision + Gesture servers (optional) ─────────────────────────
    vision_server_node = Node(
        package="robot_skills",
        executable="vision_server",
        name="vision_server",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
        parameters=[{
            "vlm_backend": LaunchConfiguration("vlm_backend"),
            "vlm_model": LaunchConfiguration("vlm_model"),
            "vlm_host": LaunchConfiguration("vlm_host"),
        }],
        condition=IfCondition(LaunchConfiguration("launch_vision")),
    )

    gesture_server_node = Node(
        package="robot_skills",
        executable="gesture_server",
        name="gesture_server",
        output="screen",
        emulate_tty=True,
        arguments=ros_args,
        condition=IfCondition(LaunchConfiguration("launch_vision")),
    )

    startup_msg = LogInfo(msg=[
        "\n",
        "======================================================\n",
        "          ANDR Tools launching\n",
        "  tool_manager          (C++)\n",
        "  speak_server          /tools/speak\n",
        "  walk_server           /tools/walk\n",
        "  spin_server           /tools/spin\n",
        "  navigate_to_point     /tools/navigate_to_point\n",
        "  go_to_point           /tools/go_to_point\n",
        "  map_server            /tools/map\n",
        "  vision_server         /tools/describe_scene  (if launch_vision)\n",
        "  gesture_server        /tools/gesture          (if launch_vision)\n",
        "======================================================\n",
    ])

    return LaunchDescription([
        *args,
        startup_msg,
        tool_manager_node,
        speak_server_node,
        walk_server_node,
        spin_server_node,
        navigate_to_point_server_node,
        go_to_point_server_node,
        map_server_node,
        vision_server_node,
        gesture_server_node,
    ])
