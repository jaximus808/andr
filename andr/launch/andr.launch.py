"""
andr.launch.py — Launch the full ANDR stack.

Usage
-----
# Full stack, Ollama LLM (default for testing)
ros2 launch andr andr.launch.py

# Real Ollama LLM with specific model
ros2 launch andr andr.launch.py llm_model:=qwen2.5

# Disable the brain node (agent only)
ros2 launch andr andr.launch.py launch_brain:=false

# Brain running but no autonomous wander/BT
ros2 launch andr andr.launch.py enable_wander:=false

# UI only (no brain, agent, or skills)
ros2 launch andr andr.launch.py launch_brain:=false launch_agent:=false launch_skills:=false

Launch arguments
----------------
launch_brain        bool    true        Start the andr_brain C++ node.
enable_wander       bool    true        Enable the behavior tree / wander loop inside the brain.
launch_agent        bool    true        Start the agent_server Python node.
launch_task_mgr     bool    true        Start the task_manager_server Python node.
launch_skills       bool    true        Start skill_executor + mock skill servers.
launch_ui           bool    true        Start the andr_ui web dashboard.
ui_port             string  8080        Port for the andr_ui web server.
log_level           string  info        ROS log level (debug/info/warn/error).

llm_backend         string  ollama      LLM backend: ollama | openai
llm_model           string  llama3.2    Model name (e.g. llama3.2, qwen2.5, gpt-4o).
llm_host            string  http://localhost:11434
llm_temperature     double  0.2

memory_backend      string  chroma      RAG backend: chroma
memory_top_k        int     4

skills_yaml         string  <installed share path>
max_iterations      int     20
"""

from __future__ import annotations

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    LogInfo,
    OpaqueFunction,
)
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def _agent_node(context, *args, **kwargs) -> list:
    """Build the agent_server Node with all parameters resolved."""

    def cfg(name: str):
        return LaunchConfiguration(name).perform(context)

    # skills_yaml: prefer the explicit arg, fall back to the installed share path
    skills_yaml = cfg("skills_yaml")
    if not skills_yaml:
        from ament_index_python.packages import get_package_share_directory
        import os
        skills_yaml = os.path.join(
            get_package_share_directory("andr"), "skills.yaml"
        )

    node = Node(
        package="agent",
        executable="agent_server",
        name="agent_server",
        output="screen",
        emulate_tty=True,
        arguments=["--ros-args", "--log-level", cfg("log_level")],
        parameters=[{
            "llm_backend":       cfg("llm_backend"),
            "llm_model":         cfg("llm_model"),
            "llm_host":          cfg("llm_host"),
            "llm_temperature":   float(cfg("llm_temperature")),
            "skills_yaml":       skills_yaml,
            "memory_backend":    cfg("memory_backend"),
            "memory_top_k":      int(cfg("memory_top_k")),
            "max_iterations":    int(cfg("max_iterations")),
        }],
    )
    return [node]


def _skill_executor_node(context, *args, **kwargs) -> list:
    """Build the skill_executor C++ node with the config path resolved."""
    from ament_index_python.packages import get_package_share_directory
    import os

    config_yaml = os.path.join(
        get_package_share_directory("andr"), "skill_executor_config.yaml"
    )

    node = Node(
        package="skill_executor",
        executable="skill_executor_node",
        name="skill_executor",
        output="screen",
        emulate_tty=True,
        parameters=[{"config_yaml": config_yaml}],
    )
    return [node]


def generate_launch_description() -> LaunchDescription:

    args = [
        # ── Node toggles ──────────────────────────────────────────────
        DeclareLaunchArgument("launch_brain", default_value="true",
                              description="Start the andr_brain C++ node"),
        DeclareLaunchArgument("enable_wander", default_value="true",
                              description="Enable behavior tree / wander loop in brain"),
        DeclareLaunchArgument("launch_agent", default_value="true",
                              description="Start the agent_server Python node"),
        DeclareLaunchArgument("launch_task_mgr", default_value="true",
                              description="Start the task_manager_server node"),
        DeclareLaunchArgument("launch_skills", default_value="true",
                              description="Start skill_executor + mock skill servers"),
        DeclareLaunchArgument("launch_ui",    default_value="true",
                              description="Start the andr_ui web dashboard"),
        DeclareLaunchArgument("ui_port",      default_value="8080",
                              description="Port for the andr_ui web server"),
        DeclareLaunchArgument("log_level",    default_value="info",
                              choices=["debug", "info", "warn", "error"],
                              description="ROS log level for all nodes"),

        # ── LLM ───────────────────────────────────────────────────────
        DeclareLaunchArgument("llm_backend",     default_value="ollama",
                              description="ollama | openai"),
        DeclareLaunchArgument("llm_model",       default_value="llama3.2",
                              description="Model name (e.g. llama3.2, qwen2.5, gpt-4o)"),
        DeclareLaunchArgument("llm_host",        default_value="http://localhost:11434"),
        DeclareLaunchArgument("llm_temperature", default_value="0.2"),

        # ── Memory / RAG ──────────────────────────────────────────────
        DeclareLaunchArgument("memory_backend",    default_value="chroma",
                              description="chroma"),
        DeclareLaunchArgument("memory_top_k",      default_value="4"),

        # ── Skills ────────────────────────────────────────────────────
        DeclareLaunchArgument("skills_yaml", default_value="",
                              description="Path to skills.yaml (default: installed share)"),

        # ── Loop tuning ───────────────────────────────────────────────
        DeclareLaunchArgument("max_iterations",  default_value="20"),
    ]

    brain_node = Node(
        package="andr",
        executable="andr_brain",
        name="andr_brain",
        output="screen",
        emulate_tty=True,
        arguments=["--ros-args", "--log-level",
                   LaunchConfiguration("log_level")],
        parameters=[{"enable_wander": LaunchConfiguration("enable_wander")}],
        condition=IfCondition(LaunchConfiguration("launch_brain")),
    )

    agent_node_action = OpaqueFunction(
        function=_agent_node,
        condition=IfCondition(LaunchConfiguration("launch_agent")),
    )

    task_manager_node = Node(
        package="task_manager",
        executable="task_manager_server",
        name="task_manager",
        output="screen",
        emulate_tty=True,
        arguments=["--ros-args", "--log-level",
                   LaunchConfiguration("log_level")],
        condition=IfCondition(LaunchConfiguration("launch_task_mgr")),
    )

    ui_process = ExecuteProcess(
        cmd=["python3", "-m", "andr_ui.server"],
        output="screen",
        emulate_tty=True,
        additional_env={"ANDR_UI_PORT": LaunchConfiguration("ui_port")},
        condition=IfCondition(LaunchConfiguration("launch_ui")),
    )

    # ── Skill executor + mock skill servers ─────────────────────────────
    skill_executor_node = OpaqueFunction(
        function=_skill_executor_node,
        condition=IfCondition(LaunchConfiguration("launch_skills")),
    )

    speak_server_node = Node(
        package="robot_skills",
        executable="speak_server",
        name="speak_server",
        output="screen",
        emulate_tty=True,
        condition=IfCondition(LaunchConfiguration("launch_skills")),
    )

    walk_server_node = Node(
        package="robot_skills",
        executable="walk_server",
        name="walk_server",
        output="screen",
        emulate_tty=True,
        condition=IfCondition(LaunchConfiguration("launch_skills")),
    )

    spin_server_node = Node(
        package="robot_skills",
        executable="spin_server",
        name="spin_server",
        output="screen",
        emulate_tty=True,
        condition=IfCondition(LaunchConfiguration("launch_skills")),
    )

    navigate_to_point_server_node = Node(
        package="robot_skills",
        executable="navigate_to_point_server",
        name="navigate_to_point_server",
        output="screen",
        emulate_tty=True,
        condition=IfCondition(LaunchConfiguration("launch_skills")),
    )

    startup_msg = LogInfo(msg=[
        "\n",
        "======================================================\n",
        "          ANDR Stack launching\n",
        "  brain   -> andr_brain\n",
        "  agent   -> agent_server  (agent/prompt)\n",
        "  tasks   -> task_manager  (/task_manager/execute)\n",
        "  ui      -> http://localhost:",
        LaunchConfiguration("ui_port"), "\n",
        "======================================================\n",
        "  llm_backend   = ", LaunchConfiguration("llm_backend"),  "\n",
        "  llm_model     = ", LaunchConfiguration("llm_model"),    "\n",
        "  memory_backend= ", LaunchConfiguration("memory_backend"), "\n",
        "  log_level     = ", LaunchConfiguration("log_level"),   "\n",
    ])

    return LaunchDescription([
        *args, startup_msg,
        brain_node, agent_node_action, task_manager_node, ui_process,
        skill_executor_node, speak_server_node, walk_server_node, spin_server_node,
        navigate_to_point_server_node,
    ])
