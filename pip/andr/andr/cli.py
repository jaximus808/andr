"""andr CLI — start and interact with the ANDR agent stack.

Usage:
    andr start                          # start with defaults
    andr start --backend openai --model gpt-4o
    andr start --tools speak,walk,spin

    andr status                         # check what's running
    andr task "Say hello"               # send a task to the agent
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import shutil
import signal
import subprocess
import sys
import time


# Maps tool shortnames to (package, executable)
TOOL_MAP = {
    "speak": ("andr_skills", "speak_server"),
    "gesture": ("andr_skills", "gesture_server"),
    "vision": ("andr_skills", "vision_server"),
    "walk": ("andr_nav", "walk_server"),
    "spin": ("andr_nav", "spin_server"),
    "navigate_to_point": ("andr_nav", "navigate_to_point_server"),
    "map": ("andr_nav", "map_server"),
}


def _check_ros():
    """Verify ROS 2 is available."""
    if shutil.which("ros2") is None:
        print("Error: 'ros2' command not found.")
        print("Make sure ROS 2 is installed and sourced:")
        print("  source /opt/ros/humble/setup.bash")
        sys.exit(1)


# ── Node runner functions (each runs in its own process) ────────────────

def _run_prompt_manager():
    """Run prompt_manager node in-process."""
    import rclpy
    from andr.runtime.agent.prompt_manager import PromptManagerNode
    logging.basicConfig(level=logging.INFO)
    rclpy.init()
    node = PromptManagerNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, Exception):
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


def _run_ui(port):
    """Run the web UI in-process.

    The UI server (server.py) starts its own rclpy context inside
    ros_bridge.start_ros_thread(), so we must NOT call rclpy.init() here.
    """
    os.environ["ANDR_UI_PORT"] = str(port)
    from andr.runtime.andr_ui.server import main as ui_main
    try:
        ui_main()
    except (KeyboardInterrupt, Exception):
        pass


def _run_task_manager():
    """Run task_manager node in-process."""
    import rclpy
    from rclpy.executors import MultiThreadedExecutor
    from andr.runtime.task_manager.task_manager_server import TaskManagerServer
    logging.basicConfig(level=logging.DEBUG)
    rclpy.init()
    server = TaskManagerServer()
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    try:
        executor.spin()
    except (KeyboardInterrupt, Exception):
        pass
    finally:
        server.destroy()
        rclpy.shutdown()


def _run_agent(backend, model, host, temperature, max_iterations):
    """Run agent_server node in-process."""
    import rclpy
    from rclpy.executors import MultiThreadedExecutor

    logging.basicConfig(level=logging.DEBUG)

    # Pass parameters via ROS args so they're set BEFORE the node's __init__
    # reads them (AgentServer calls _setup_langchain in __init__)
    ros_args = [
        "--ros-args",
        "-p", f"llm_backend:={backend}",
        "-p", f"llm_host:={host}",
        "-p", f"llm_temperature:={temperature}",
        "-p", f"max_iterations:={max_iterations}",
    ]
    if model:
        ros_args.extend(["-p", f"llm_model:={model}"])

    rclpy.init(args=ros_args)

    # Import after rclpy.init so ROS context is ready
    from andr.runtime.agent.agent import AgentServer

    server = AgentServer()
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    try:
        executor.spin()
    except (KeyboardInterrupt, Exception):
        pass
    finally:
        server.destroy()
        rclpy.shutdown()


def _run_task_brain(enable_wander, wander_interval, resume_preempted):
    """Run task_brain node in-process."""
    import rclpy
    from rclpy.executors import MultiThreadedExecutor

    logging.basicConfig(level=logging.INFO)

    ros_args = [
        "--ros-args",
        "-p", f"enable_wander:={'true' if enable_wander else 'false'}",
        "-p", f"wander_interval_sec:={wander_interval}",
        "-p", f"enable_scheduler:=true",
        "-p", f"resume_preempted:={'true' if resume_preempted else 'false'}",
    ]
    rclpy.init(args=ros_args)

    from andr.runtime.task_manager.task_brain import TaskBrain

    brain = TaskBrain()
    executor = MultiThreadedExecutor()
    executor.add_node(brain)
    try:
        executor.spin()
    except (KeyboardInterrupt, Exception):
        pass
    finally:
        brain.destroy()
        rclpy.shutdown()


def _find_tool_manager():
    """Find tool_manager_node binary.

    Search order:
      1. ANDR_TOOL_MANAGER_BIN env var
      2. Colcon install directory (auto-detected repo root)
      3. Bundled binary inside the pip package
      4. ROS 2 workspace (ros2 run)
      5. System PATH
    """
    # 1. Explicit env var
    env_bin = os.environ.get("ANDR_TOOL_MANAGER_BIN")
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    # 2. Colcon install (auto-detect from repo root)
    from andr._setup_msgs import _find_repo_root
    repo_root = _find_repo_root()
    if repo_root:
        colcon_bin = os.path.join(
            repo_root, "install", "tool_manager", "lib",
            "tool_manager", "tool_manager_node",
        )
        if os.path.isfile(colcon_bin):
            return colcon_bin

    # 3. Bundled binary (shipped with pip install andr)
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    bundled = os.path.join(pkg_dir, "bin", "tool_manager_node")
    if os.path.isfile(bundled):
        return bundled

    # 4. ROS 2 workspace
    result = subprocess.run(
        ["ros2", "pkg", "prefix", "tool_manager"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return "ros2_run"

    # 5. System PATH
    path_bin = shutil.which("tool_manager_node")
    if path_bin:
        return path_bin

    return None


def _ros2_pkg_exists(package):
    """Check if a ROS 2 package is available."""
    result = subprocess.run(
        ["ros2", "pkg", "prefix", package],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _launch_tool_via_ros2(package, executable):
    """Launch a tool via ros2 run (for tools from colcon workspace)."""
    return subprocess.Popen(
        ["ros2", "run", package, executable],
        stderr=subprocess.DEVNULL,
    )


def cmd_start(args):
    """Start the ANDR agent stack."""
    _check_ros()

    # Ensure our bundled andr_msgs are set up
    from andr._setup_msgs import setup as _setup_msgs
    _setup_msgs()

    processes = []   # subprocess.Popen instances
    mp_procs = []    # multiprocessing.Process instances

    shutting_down = False

    def shutdown(sig=None, frame=None):
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        print("\nShutting down ANDR...")
        for p in reversed(mp_procs):
            if p.is_alive():
                p.terminate()
        for p in reversed(processes):
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        for p in mp_procs:
            p.join(timeout=5)
        for p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("Starting ANDR stack...")
    print(f"  Backend:  {args.backend}")
    print(f"  Model:    {args.model or '(default)'}")
    print(f"  Host:     {args.host}")
    print()

    # 1. Tool manager (C++ binary — needs colcon or prebuilt)
    tm = _find_tool_manager()
    if tm is None:
        print("  [skip] tool_manager not found — tools won't auto-register")
        print("         Set ANDR_TOOL_MANAGER_BIN or build from source")
    elif tm == "ros2_run":
        print("  [ok]   Starting tool_manager...")
        processes.append(subprocess.Popen(
            ["ros2", "run", "tool_manager", "tool_manager_node"]
        ))
    else:
        print("  [ok]   Starting tool_manager...")
        # Set LD_LIBRARY_PATH so the binary can find andr_msgs + ROS .so files
        tm_env = os.environ.copy()
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        libs_dir = os.path.join(pkg_dir, "_libs")
        from andr._setup_msgs import _find_ros_lib_dirs
        extra_dirs = [libs_dir] + _find_ros_lib_dirs()
        ld_path = tm_env.get("LD_LIBRARY_PATH", "")
        tm_env["LD_LIBRARY_PATH"] = ":".join(extra_dirs) + ":" + ld_path
        processes.append(subprocess.Popen([tm], env=tm_env))

    time.sleep(1)

    # 2. Prompt manager (Python — runs from bundled code)
    print("  [ok]   Starting prompt_manager...")
    p = multiprocessing.Process(target=_run_prompt_manager, daemon=True)
    p.start()
    mp_procs.append(p)
    time.sleep(1)

    # 3. Task manager (Python — runs from bundled code)
    print("  [ok]   Starting task_manager...")
    p = multiprocessing.Process(target=_run_task_manager, daemon=True)
    p.start()
    mp_procs.append(p)
    time.sleep(1)

    # 4. Task brain (Python — priority scheduler, preemption, wander)
    if not args.no_brain:
        wander = args.enable_wander
        wander_label = f" wander={'on' if wander else 'off'}"
        print(f"  [ok]   Starting task_brain...{wander_label}")
        p = multiprocessing.Process(
            target=_run_task_brain,
            args=(wander, args.wander_interval, not args.no_resume),
            daemon=True,
        )
        p.start()
        mp_procs.append(p)

    # 5. Agent (Python — runs from bundled code)
    print("  [ok]   Starting agent...")
    p = multiprocessing.Process(
        target=_run_agent,
        args=(args.backend, args.model, args.host, args.temperature, args.max_iterations),
        daemon=True,
    )
    p.start()
    mp_procs.append(p)

    # 6. Tools (need andr-robo or colcon workspace)
    if args.tools:
        tool_names = [t.strip() for t in args.tools.split(",")]
        for name in tool_names:
            if name not in TOOL_MAP:
                print(f"  [skip] Unknown tool '{name}'. Available: {', '.join(TOOL_MAP)}")
                continue
            pkg, exe = TOOL_MAP[name]
            if not _ros2_pkg_exists(pkg):
                print(f"  [skip] Tool '{name}' — package '{pkg}' not installed")
                continue
            processes.append(_launch_tool_via_ros2(pkg, exe))
            print(f"  [ok]   Starting tool: {name}")

    # 7. UI (bundled — runs from pip package)
    if not args.no_ui:
        print(f"  [ok]   Starting UI on port {args.ui_port}")
        p = multiprocessing.Process(target=_run_ui, args=(args.ui_port,), daemon=True)
        p.start()
        mp_procs.append(p)

    total = len(processes) + len(mp_procs)
    print()
    print(f"ANDR is running! ({total} processes)")
    if not args.no_ui:
        print(f"  Web UI: http://localhost:{args.ui_port}")
    print("  Press Ctrl+C to stop.")
    print()

    # Wait forever until Ctrl+C
    try:
        while True:
            # Check if core processes (agent, task_manager) are still alive
            alive = [p for p in mp_procs if p.is_alive()]
            if not alive:
                print("All core processes have exited.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown()


def cmd_task(args):
    """Send a task to the running agent."""
    _check_ros()

    prompt = " ".join(args.prompt)
    if not prompt:
        print("Error: no prompt provided.")
        print("Usage: andr task \"Say hello\"")
        sys.exit(1)

    # Use the bundled andr_msgs to send directly via rclpy
    from andr._setup_msgs import setup as _setup_msgs
    _setup_msgs()

    import rclpy
    from rclpy.action import ActionClient
    from andr_msgs.action import TaskGoal

    rclpy.init()
    node = rclpy.create_node("andr_cli_client")
    client = ActionClient(node, TaskGoal, "/task_manager/execute")

    print(f"Sending task: {prompt}")
    if not client.wait_for_server(timeout_sec=10.0):
        print("Error: task_manager not available. Is ANDR running?")
        node.destroy_node()
        rclpy.shutdown()
        sys.exit(1)

    goal = TaskGoal.Goal()
    goal.prompt = prompt
    goal.context = args.context

    future = client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)

    goal_handle = future.result()
    if goal_handle is None or not goal_handle.accepted:
        print("Error: task rejected by task_manager.")
        node.destroy_node()
        rclpy.shutdown()
        sys.exit(1)

    print("Task accepted. Waiting for result...")
    result_future = goal_handle.get_result_async()
    rclpy.spin_until_future_complete(node, result_future)

    wrapped = result_future.result()
    if wrapped is None:
        print("Error: no result returned.")
    else:
        result = wrapped.result
        status = "OK" if result.success else "FAILED"
        print(f"[{status}] {result.summary}")

    node.destroy_node()
    rclpy.shutdown()


def cmd_status(args):
    """Check what ANDR nodes are running."""
    _check_ros()

    print("=== ANDR Node Status ===\n")

    result = subprocess.run(
        ["ros2", "node", "list"], capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("Could not list ROS 2 nodes. Is ROS 2 running?")
        sys.exit(1)

    nodes = result.stdout.strip().split("\n") if result.stdout.strip() else []

    andr_nodes = {
        "agent_server": "Agent (LLM ReAct loop)",
        "task_brain": "Task Brain (scheduler/preemption)",
        "task_manager": "Task Manager",
        "tool_manager": "Tool Manager",
        "prompt_manager": "Prompt Manager",
        "ui_server": "Web UI",
    }

    for node_name, desc in andr_nodes.items():
        found = any(node_name in n for n in nodes)
        marker = "+" if found else "-"
        print(f"  [{marker}] {desc}")

    tool_nodes = [n for n in nodes if "_tool" in n]
    if tool_nodes:
        print(f"\n  Tools ({len(tool_nodes)}):")
        for t in tool_nodes:
            print(f"    + {t}")
    else:
        print("\n  No tool nodes detected.")
    print()


def cmd_init(args):
    """Scaffold a new ANDR project workspace."""
    name = args.name
    project_dir = os.path.join(os.getcwd(), name)

    if os.path.exists(project_dir):
        print(f"Error: directory '{name}' already exists.")
        sys.exit(1)

    print(f"Creating ANDR project: {name}/")

    # Create directory structure
    os.makedirs(os.path.join(project_dir, "tools"))
    os.makedirs(os.path.join(project_dir, "inputs"))

    # --- andr.config.yaml ---
    _write(project_dir, "andr.config.yaml", f"""\
# {name} — ANDR project configuration
#
# Start with: python start.py
#             or: andr start --model llama3.2

# LLM settings
llm:
  backend: ollama            # ollama | openai
  model: llama3.2            # model name
  host: http://localhost:11434
  temperature: 0.2

# Agent settings
agent:
  max_iterations: 20

# Task brain (scheduler, preemption, wander)
brain:
  enabled: true
  enable_wander: false           # Send idle prompts when no tasks pending
  wander_interval_sec: 60.0     # Seconds between wander prompts
  resume_preempted: true         # Resume interrupted tasks after higher-priority ones finish
  # wander_prompt: "Look around and describe what you see."  # Custom wander prompt

# Scheduled tasks (run automatically on a timer)
# scheduled_tasks:
#   check_battery:
#     prompt: "Check your battery level and report it."
#     interval_sec: 300          # Every 5 minutes
#   patrol:
#     prompt: "Do a patrol of the area and report anything unusual."
#     interval_sec: 600          # Every 10 minutes

# Web UI
ui:
  enabled: true
  port: 8080

# System prompt (optional — override the default)
# system_prompt: |
#   You are a helpful robot assistant...

# Tools to auto-launch from andr-robo (requires colcon workspace)
# Uncomment the tools you have installed:
# tools:
#   - speak
#   - walk
#   - spin
#   - navigate_to_point
#   - map
""")

    # --- start.py ---
    _write(project_dir, "start.py", f"""\
#!/usr/bin/env python3
\"\"\"Start the {name} ANDR project.

Reads configuration from andr.config.yaml and launches the full stack.
Also auto-discovers and launches any tools in tools/ and inputs in inputs/.

Usage:
    python start.py
\"\"\"

import glob
import importlib.util
import multiprocessing
import os
import sys

import yaml


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "andr.config.yaml")
    if not os.path.exists(config_path):
        return {{}}
    with open(config_path) as f:
        return yaml.safe_load(f) or {{}}


def discover_modules(directory):
    \"\"\"Find all .py files in a directory that aren't __init__.py.\"\"\"
    pattern = os.path.join(os.path.dirname(__file__), directory, "*.py")
    return [
        f for f in glob.glob(pattern)
        if not os.path.basename(f).startswith("_")
    ]


def run_module(filepath):
    \"\"\"Import and run a module's main() function in a subprocess.\"\"\"
    spec = importlib.util.spec_from_file_location("_mod", filepath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "main"):
        mod.main()
    else:
        print(f"Warning: {{filepath}} has no main() function, skipping.")


def main():
    config = load_config()
    llm = config.get("llm", {{}})
    agent = config.get("agent", {{}})
    ui = config.get("ui", {{}})
    brain = config.get("brain", {{}})

    # Build andr start args
    start_args = [
        "andr", "start",
        "--backend", llm.get("backend", "ollama"),
        "--model", llm.get("model", "llama3.2"),
        "--host", llm.get("host", "http://localhost:11434"),
        "--temperature", str(llm.get("temperature", 0.2)),
        "--max-iterations", str(agent.get("max_iterations", 20)),
        "--ui-port", str(ui.get("port", 8080)),
        "--wander-interval", str(brain.get("wander_interval_sec", 60.0)),
    ]

    if not ui.get("enabled", True):
        start_args.append("--no-ui")

    if not brain.get("enabled", True):
        start_args.append("--no-brain")

    if brain.get("enable_wander", False):
        start_args.append("--enable-wander")

    if not brain.get("resume_preempted", True):
        start_args.append("--no-resume")

    # Auto-launch tools from andr-robo
    tools_list = config.get("tools", [])
    if tools_list:
        start_args.extend(["--tools", ",".join(tools_list)])

    # Discover custom tools and inputs
    tool_files = discover_modules("tools")
    input_files = discover_modules("inputs")

    # Launch custom tools/inputs as separate processes
    procs = []
    for f in tool_files + input_files:
        name = os.path.basename(f)
        print(f"  Launching custom module: {{name}}")
        p = multiprocessing.Process(target=run_module, args=(f,), daemon=True)
        p.start()
        procs.append(p)

    # Run andr start (this blocks)
    sys.argv = start_args
    from andr.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
""")

    # --- tools/__init__.py ---
    _write(project_dir, "tools/__init__.py", "")

    # --- tools/example_tool.py ---
    _write(project_dir, "tools/example_tool.py", """\
\"\"\"Example custom tool.

Rename this file and modify it to create your own tool.
The tool auto-registers with the running ANDR tool_manager.

Run standalone:  python -m tools.example_tool
Or let start.py auto-discover it.
\"\"\"

from andr import BaseAgentTool


class ExampleTool(BaseAgentTool):
    TOOL_NAME = "example"
    TOOL_DESCRIPTION = "An example tool — replace with your own logic"
    TOOL_PARAMETERS = [
        {
            "name": "message",
            "type": "string",
            "required": True,
            "description": "A message to process",
        },
    ]

    def _execute(self, params, goal_handle):
        message = params.get("message", "")
        self.get_logger().info(f"ExampleTool received: {message}")

        # Your tool logic here
        result = f"Processed: {message}"

        return {"status": "done", "result": result}


def main(args=None):
    import rclpy
    rclpy.init(args=args)
    node = ExampleTool()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
""")

    # --- inputs/__init__.py ---
    _write(project_dir, "inputs/__init__.py", "")

    # --- inputs/example_input.py ---
    _write(project_dir, "inputs/example_input.py", """\
\"\"\"Example custom input source.

Rename this file and modify it to create your own input source.
Input sources send tasks to the agent through the task_manager.

Run standalone:  python -m inputs.example_input
Or let start.py auto-discover it.
\"\"\"

from andr import BaseInputSource
from std_msgs.msg import String


class ExampleInput(BaseInputSource):
    SOURCE_NAME = "example"
    SOURCE_DESCRIPTION = "An example input source — replace with your own logic"

    def __init__(self):
        super().__init__()
        # Subscribe to a topic, poll an API, watch a file, etc.
        # This example listens on a ROS topic:
        self.create_subscription(
            String, "/example/input", self._on_message, 10
        )

    def _on_message(self, msg):
        if not self.is_busy:
            self.send_task(
                prompt=msg.data,
                context="example_input",
            )

    def on_task_completed(self, prompt, success, summary):
        self.get_logger().info(
            f"Task {'succeeded' if success else 'failed'}: {summary}"
        )


def main(args=None):
    import rclpy
    rclpy.init(args=args)
    node = ExampleInput()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
""")

    print(f"""
  {name}/
    andr.config.yaml      # Project configuration
    start.py              # Launch script (reads config, auto-discovers tools/inputs)
    tools/
      example_tool.py     # Example BaseAgentTool — edit or replace
    inputs/
      example_input.py    # Example BaseInputSource — edit or replace

  Get started:
    cd {name}
    python start.py
""")


def _write(base_dir, rel_path, content):
    """Write a file, creating parent dirs as needed."""
    full_path = os.path.join(base_dir, rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w") as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser(
        prog="andr",
        description="ANDR — LLM agent framework for robotics",
    )
    sub = parser.add_subparsers(dest="command")

    # --- andr init ---
    p_init = sub.add_parser("init", help="Create a new ANDR project workspace")
    p_init.add_argument("name", help="Project name (creates a directory)")

    # --- andr start ---
    p_start = sub.add_parser("start", help="Start the ANDR agent stack")
    p_start.add_argument("--backend", default="ollama", choices=["ollama", "openai"],
                         help="LLM backend (default: ollama)")
    p_start.add_argument("--model", default="", help="Model name (e.g., llama3.2, gpt-4o)")
    p_start.add_argument("--host", default="http://localhost:11434", help="Ollama server URL")
    p_start.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature")
    p_start.add_argument("--max-iterations", type=int, default=20, help="Max ReAct iterations")
    p_start.add_argument("--tools", default="", help="Comma-separated tools to launch (e.g., speak,walk,spin)")
    p_start.add_argument("--no-ui", action="store_true", help="Don't start the web UI")
    p_start.add_argument("--ui-port", type=int, default=8080, help="Web UI port (default: 8080)")
    p_start.add_argument("--no-brain", action="store_true", help="Don't start the task brain (scheduler/preemption)")
    p_start.add_argument("--enable-wander", action="store_true", help="Enable wander mode (idle prompts when no tasks)")
    p_start.add_argument("--wander-interval", type=float, default=60.0, help="Seconds between wander prompts (default: 60)")
    p_start.add_argument("--no-resume", action="store_true", help="Don't resume preempted tasks")

    # --- andr task ---
    p_task = sub.add_parser("task", help="Send a task to the running agent")
    p_task.add_argument("prompt", nargs="+", help="The task prompt")
    p_task.add_argument("--context", default="", help="Optional context string")

    # --- andr status ---
    sub.add_parser("status", help="Check which ANDR nodes are running")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "start":
        cmd_start(args)
    elif args.command == "task":
        cmd_task(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
