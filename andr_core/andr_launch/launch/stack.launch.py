"""
stack.launch.py — Config-driven launch for the full ANDR stack.

Reads a YAML config file that declares which core nodes, tools, and input
sources to launch, along with their parameters. Good for testing the full
stack with a single command.

Usage
-----
# Default config (andr/config/stack.yaml)
ros2 launch andr_launch stack.launch.py

# Custom config
ros2 launch andr_launch stack.launch.py config:=/path/to/my_stack.yaml

# Override log level
ros2 launch andr_launch stack.launch.py log_level:=debug

Launch arguments
----------------
config      string  <package>/config/stack.yaml   Path to YAML config file.
log_level   string  (from config, default info)   Override log level.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    LogInfo,
    OpaqueFunction,
)
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from ament_index_python.packages import get_package_share_directory


def _get_default_config() -> str:
    return os.path.join(
        get_package_share_directory("andr_launch"), "config", "stack.yaml"
    )


def _build_nodes(context, *args, **kwargs) -> list:
    """Read the YAML config and build all enabled nodes."""

    config_path = LaunchConfiguration("config").perform(context)
    log_override = LaunchConfiguration("log_level").perform(context)

    with open(config_path, "r") as f:
        cfg = yaml.safe_load(f)

    log_level = log_override or cfg.get("log_level", "info")
    ros_args = ["--ros-args", "--log-level", log_level]

    llm_cfg = cfg.get("llm", {})
    core = cfg.get("core", {})
    tools_cfg = cfg.get("tools", {})
    inputs_cfg = cfg.get("inputs", {})

    nodes = []
    summary_lines = [
        "\n",
        "======================================================\n",
        "          ANDR Stack (config-driven)\n",
        f"  config: {config_path}\n",
        "------------------------------------------------------\n",
    ]

    # ── Core: task_brain ───────────────────────────────────────────────────
    brain = core.get("task_brain", {})
    if brain.get("enabled", False):
        brain_params = brain.get("params", {})
        nodes.append(Node(
            package="task_manager",
            executable="task_brain",
            name="task_brain",
            output="screen",
            emulate_tty=True,
            arguments=ros_args,
            parameters=[brain_params] if brain_params else [],
        ))
        wander = brain_params.get("enable_wander", False)
        summary_lines.append(f"  [core] task_brain     (wander={'on' if wander else 'off'})\n")

    # ── Core: task_manager ───────────────────────────────────────────────
    if core.get("task_manager", {}).get("enabled", False):
        nodes.append(Node(
            package="task_manager",
            executable="task_manager_server",
            name="task_manager",
            output="screen",
            emulate_tty=True,
            arguments=ros_args,
        ))
        summary_lines.append("  [core] task_manager\n")

    # ── Core: prompt_manager ─────────────────────────────────────────────
    if core.get("prompt_manager", {}).get("enabled", False):
        nodes.append(Node(
            package="agent",
            executable="prompt_manager",
            name="prompt_manager",
            output="screen",
            emulate_tty=True,
            arguments=ros_args,
        ))
        summary_lines.append("  [core] prompt_manager\n")

    # ── Core: memory node ─────────────────────────────────────────────────
    mem_cfg = core.get("memory", {})
    if mem_cfg.get("enabled", False):
        mem_pkg = mem_cfg.get("package", "agent")
        mem_exe = mem_cfg.get("executable", "memory_chroma_node")
        mem_params = mem_cfg.get("params", {})
        nodes.append(Node(
            package=mem_pkg,
            executable=mem_exe,
            name=mem_exe,
            output="screen",
            emulate_tty=True,
            arguments=ros_args,
            parameters=[mem_params] if mem_params else [],
        ))
        summary_lines.append(f"  [core] memory         ({mem_exe})\n")

    # ── Core: agent ──────────────────────────────────────────────────────
    agent_cfg = core.get("agent", {})
    if agent_cfg.get("enabled", False):
        agent_params = agent_cfg.get("params", {})
        agent_params.update({
            "llm_backend": llm_cfg.get("backend", "ollama"),
            "llm_model": llm_cfg.get("model", "qwen2.5:3b"),
            "llm_host": llm_cfg.get("host", "http://localhost:11434"),
            "llm_temperature": float(llm_cfg.get("temperature", 0.2)),
        })
        nodes.append(Node(
            package="agent",
            executable="agent_server",
            name="agent_server",
            output="screen",
            emulate_tty=True,
            arguments=ros_args,
            parameters=[agent_params],
        ))
        summary_lines.append(
            f"  [core] agent          ({llm_cfg.get('backend', 'ollama')}/"
            f"{llm_cfg.get('model', 'qwen2.5:3b')})\n"
        )

    # ── Core: tool_manager ───────────────────────────────────────────────
    if core.get("tool_manager", {}).get("enabled", False):
        nodes.append(Node(
            package="tool_manager",
            executable="tool_manager_node",
            name="tool_manager",
            output="screen",
            emulate_tty=True,
            arguments=ros_args,
        ))
        summary_lines.append("  [core] tool_manager\n")

    # ── Core: UI ─────────────────────────────────────────────────────────
    ui_cfg = core.get("ui", {})
    if ui_cfg.get("enabled", False):
        port = str(ui_cfg.get("port", "8080"))
        nodes.append(ExecuteProcess(
            cmd=["python3", "-m", "andr_ui.server"],
            output="screen",
            emulate_tty=True,
            additional_env={"ANDR_UI_PORT": port},
        ))
        summary_lines.append(f"  [core] ui             http://localhost:{port}\n")

    summary_lines.append("------------------------------------------------------\n")

    # ── Tools ────────────────────────────────────────────────────────────
    for tool_name, tool_cfg in tools_cfg.items():
        if not tool_cfg.get("enabled", False):
            summary_lines.append(f"  [tool] {tool_name:<16} DISABLED\n")
            continue

        pkg = tool_cfg.get("package", "robot_skills")
        exe = tool_cfg.get("executable", f"{tool_name}_server")
        params = tool_cfg.get("params", {})

        nodes.append(Node(
            package=pkg,
            executable=exe,
            name=exe,
            output="screen",
            emulate_tty=True,
            arguments=ros_args,
            parameters=[params] if params else [],
        ))
        summary_lines.append(f"  [tool] {tool_name:<16} /tools/{tool_name}\n")

    summary_lines.append("------------------------------------------------------\n")

    # ── Input sources ────────────────────────────────────────────────────
    for input_name, input_cfg in inputs_cfg.items():
        if not input_cfg.get("enabled", False):
            summary_lines.append(f"  [input] {input_name:<15} DISABLED\n")
            continue

        pkg = input_cfg.get("package", "robot_skills")
        exe = input_cfg.get("executable", f"{input_name}_server")
        params = input_cfg.get("params", {})

        nodes.append(Node(
            package=pkg,
            executable=exe,
            name=exe,
            output="screen",
            emulate_tty=True,
            arguments=ros_args,
            parameters=[params] if params else [],
        ))
        summary_lines.append(f"  [input] {input_name:<15} active\n")

    summary_lines.append("======================================================\n")

    return [LogInfo(msg=summary_lines)] + nodes


def generate_launch_description() -> LaunchDescription:
    return LaunchDescription([
        DeclareLaunchArgument(
            "config",
            default_value=_get_default_config(),
            description="Path to YAML stack configuration file",
        ),
        DeclareLaunchArgument(
            "log_level",
            default_value="",
            description="Override log level from config (debug/info/warn/error)",
        ),
        OpaqueFunction(function=_build_nodes),
    ])
