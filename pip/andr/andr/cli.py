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
      2. Bundled binary inside the pip package
      3. ROS 2 workspace (ros2 run)
      4. System PATH
    """
    # 1. Explicit env var
    env_bin = os.environ.get("ANDR_TOOL_MANAGER_BIN")
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    # 2. Bundled binary (shipped with pip install andr)
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    bundled = os.path.join(pkg_dir, "bin", "tool_manager_node")
    if os.path.isfile(bundled):
        return bundled

    # 3. ROS 2 workspace
    result = subprocess.run(
        ["ros2", "pkg", "prefix", "tool_manager"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return "ros2_run"

    # 4. System PATH
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


def _load_config(path):
    """Load andr.config.yaml and return the parsed dict (or {})."""
    if not os.path.isfile(path):
        return {}
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        print("Warning: PyYAML not installed — cannot read config file.")
        return {}
    except Exception as e:
        print(f"Warning: failed to read {path}: {e}")
        return {}


def _apply_config(args, config):
    """Apply config-file values as defaults — CLI flags take precedence."""
    llm = config.get("llm", {})
    agent = config.get("agent", {})
    ui = config.get("ui", {})
    brain = config.get("brain", {})

    # Only override if the CLI arg is still at its default value
    if not args.model and llm.get("model"):
        args.model = str(llm["model"])
    if args.backend == "ollama" and llm.get("backend"):
        args.backend = llm["backend"]
    if args.host == "http://localhost:11434" and llm.get("host"):
        args.host = llm["host"]
    if args.temperature == 0.2 and llm.get("temperature") is not None:
        args.temperature = float(llm["temperature"])
    if args.max_iterations == 20 and agent.get("max_iterations") is not None:
        args.max_iterations = int(agent["max_iterations"])
    if args.ui_port == 8080 and ui.get("port") is not None:
        args.ui_port = int(ui["port"])
    if not args.no_ui and ui.get("enabled") is False:
        args.no_ui = True
    if not args.no_brain and brain.get("enabled") is False:
        args.no_brain = True
    if not args.enable_wander and brain.get("enable_wander"):
        args.enable_wander = True
    if args.wander_interval == 60.0 and brain.get("wander_interval_sec") is not None:
        args.wander_interval = float(brain["wander_interval_sec"])
    if not args.no_resume and brain.get("resume_preempted") is False:
        args.no_resume = True


def cmd_start(args):
    """Start the ANDR agent stack."""
    _check_ros()

    # Load config file (auto-detect in cwd, or explicit --config path)
    config_path = getattr(args, "config", None) or os.path.join(os.getcwd(), "andr.config.yaml")
    config = _load_config(config_path)
    if config:
        print(f"  Config:   loaded from {config_path}")
        _apply_config(args, config)
    else:
        print(f"  Config:   no config file found (using CLI defaults)")
        print(f"            looked for: {config_path}")

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

    if not args.model:
        print("Error: no model specified.")
        print("Either:")
        print("  1. Set 'model' in andr.config.yaml  (run 'andr init' to create one)")
        print("  2. Pass --model on the CLI:  andr start --model qwen2.5:7b")
        sys.exit(1)

    print("Starting ANDR stack...")
    print(f"  Backend:  {args.backend}")
    print(f"  Model:    {args.model}")
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
        # Set LD_LIBRARY_PATH so the binary can find bundled andr_msgs .so files
        tm_env = os.environ.copy()
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        libs_dir = os.path.join(pkg_dir, "_libs")
        ld_path = tm_env.get("LD_LIBRARY_PATH", "")
        tm_env["LD_LIBRARY_PATH"] = libs_dir + ":" + ld_path
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

    # ── Locate bundled template and static directories ────────────────
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(pkg_dir, "templates")
    static_src = os.path.join(pkg_dir, "runtime", "andr_ui", "static")

    # ── Create directory structure ────────────────────────────────────
    os.makedirs(os.path.join(project_dir, "managers", "migrations"))
    os.makedirs(os.path.join(project_dir, "tools"))
    os.makedirs(os.path.join(project_dir, "inputs"))
    os.makedirs(os.path.join(project_dir, "runnables"))
    os.makedirs(os.path.join(project_dir, "ui", "static"))

    # ── Copy template files (managers, tools, inputs) ─────────────────
    import shutil

    # Managers
    for src_name in ("__init__.py", "map_server.py"):
        src = os.path.join(templates_dir, "managers", src_name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(project_dir, "managers", src_name))

    # Manager migrations (SQL files)
    migrations_src = os.path.join(templates_dir, "managers", "migrations")
    if os.path.isdir(migrations_src):
        for sql_file in sorted(os.listdir(migrations_src)):
            if sql_file.endswith(".sql"):
                shutil.copy2(
                    os.path.join(migrations_src, sql_file),
                    os.path.join(project_dir, "managers", "migrations", sql_file),
                )

    # Tools
    for src_name in ("__init__.py", "walk.py", "spin.py", "navigate_to_point.py"):
        src = os.path.join(templates_dir, "tools", src_name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(project_dir, "tools", src_name))

    # Inputs
    for src_name in ("__init__.py", "web_ui.py"):
        src = os.path.join(templates_dir, "inputs", src_name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(project_dir, "inputs", src_name))

    # Runnables
    for src_name in ("__init__.py",):
        src = os.path.join(templates_dir, "runnables", src_name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(project_dir, "runnables", src_name))

    # UI static files (HTML, CSS, JS)
    if os.path.isdir(static_src):
        for item in os.listdir(static_src):
            s = os.path.join(static_src, item)
            d = os.path.join(project_dir, "ui", "static", item)
            if os.path.isfile(s):
                shutil.copy2(s, d)
            elif os.path.isdir(s):
                shutil.copytree(s, d)

    # ── andr.config.yaml ──────────────────────────────────────────────
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

# Scheduled tasks (run automatically on a timer)
# scheduled_tasks:
#   check_battery:
#     prompt: "Check your battery level and report it."
#     interval_sec: 300
#   patrol:
#     prompt: "Do a patrol of the area and report anything unusual."
#     interval_sec: 600

# Web UI
ui:
  enabled: true
  port: 8080

# System prompt (optional — override the default)
# system_prompt: |
#   You are a helpful robot assistant...
""")

    # ── start.py ──────────────────────────────────────────────────────
    _write(project_dir, "start.py", f"""\
#!/usr/bin/env python3
\"\"\"Start the {name} ANDR project.

Reads configuration from andr.config.yaml and launches the full stack:
  - Core (hidden): tool_manager, prompt_manager, task_manager, agent, brain
  - Managers: map_server, etc. (auto-discovered from managers/)
  - Tools: walk, spin, navigate_to_point, etc. (auto-discovered from tools/)
  - Inputs: web_ui, etc. (auto-discovered from inputs/)
  - Runnables: standalone processes (auto-discovered from runnables/)

Usage:
    python start.py
\"\"\"

import glob
import importlib.util
import multiprocessing
import os
import sys
import time

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

    # Build andr start args (core stack only — no UI, tools come from local dirs)
    start_args = [
        "andr", "start",
        "--backend", llm.get("backend", "ollama"),
        "--model", llm.get("model", "llama3.2"),
        "--host", llm.get("host", "http://localhost:11434"),
        "--temperature", str(llm.get("temperature", 0.2)),
        "--max-iterations", str(agent.get("max_iterations", 20)),
        "--ui-port", str(ui.get("port", 8080)),
        "--wander-interval", str(brain.get("wander_interval_sec", 60.0)),
        "--no-ui",   # UI is handled by inputs/web_ui.py
    ]

    if not brain.get("enabled", True):
        start_args.append("--no-brain")

    if brain.get("enable_wander", False):
        start_args.append("--enable-wander")

    if not brain.get("resume_preempted", True):
        start_args.append("--no-resume")

    # Set UI port as env var for inputs/web_ui.py
    os.environ["ANDR_UI_PORT"] = str(ui.get("port", 8080))

    # Discover managers, tools, inputs, and runnables
    manager_files = discover_modules("managers")
    tool_files = discover_modules("tools")
    input_files = discover_modules("inputs")
    runnable_files = discover_modules("runnables")

    # Launch managers first (map_server, etc.) — tools may depend on them
    procs = []
    for f in manager_files:
        fname = os.path.basename(f)
        print(f"  Launching manager: {{fname}}")
        p = multiprocessing.Process(target=run_module, args=(f,), daemon=True)
        p.start()
        procs.append(p)

    if manager_files:
        time.sleep(2)  # Give managers time to register services

    # Launch tools
    for f in tool_files:
        fname = os.path.basename(f)
        print(f"  Launching tool: {{fname}}")
        p = multiprocessing.Process(target=run_module, args=(f,), daemon=True)
        p.start()
        procs.append(p)

    # Launch inputs (web_ui, etc.)
    for f in input_files:
        fname = os.path.basename(f)
        print(f"  Launching input: {{fname}}")
        p = multiprocessing.Process(target=run_module, args=(f,), daemon=True)
        p.start()
        procs.append(p)

    # Launch runnables (standalone processes)
    for f in runnable_files:
        fname = os.path.basename(f)
        print(f"  Launching runnable: {{fname}}")
        p = multiprocessing.Process(target=run_module, args=(f,), daemon=True)
        p.start()
        procs.append(p)

    # Run andr start (this blocks — starts the hidden core stack)
    sys.argv = start_args
    from andr.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()
""")

    print(f"""
  {name}/
    andr.config.yaml          # Project configuration
    start.py                  # Launch script (auto-discovers everything)
    managers/
      map_server.py           # Map management (SQLite, SLAM config, points)
      migrations/             # Database schema migrations
    tools/
      walk.py                 # Walk forward/backward via /cmd_vel
      spin.py                 # Rotate in place via /cmd_vel
      navigate_to_point.py    # Navigate to a named map point via Nav2
    inputs/
      web_ui.py               # Web dashboard + WebSocket bridge to ROS
    runnables/                # Standalone processes (auto-discovered)
    ui/
      static/                 # HTML/CSS/JS for the web dashboard
        index.html            # Robot dashboard
        rviz.html             # 2D map visualization

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
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Commands:
  init      Create a new ANDR project workspace
  start     Start the ANDR agent stack
  task      Send a task to the running agent
  status    Check which ANDR nodes are running

Run 'andr <command> -h' for detailed help on each command.
""",
    )
    sub = parser.add_subparsers(dest="command")

    # --- andr init ---
    p_init = sub.add_parser(
        "init",
        help="Create a new ANDR project workspace",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Scaffolds a new ANDR project directory with:
  andr.config.yaml          Project configuration
  start.py                  Launch script (auto-discovers everything)
  managers/                 Service managers (map_server, migrations)
  tools/                    Robot tools (walk, spin, navigate_to_point)
  inputs/                   Input sources (web_ui)
  runnables/                Standalone processes
  ui/static/                Web dashboard HTML/CSS/JS

Example:
  andr init my_robot
  cd my_robot
  python start.py
""",
    )
    p_init.add_argument("name", help="Project name (creates a directory)")

    # --- andr start ---
    available_tools = ", ".join(sorted(TOOL_MAP.keys()))
    p_start = sub.add_parser(
        "start",
        help="Start the ANDR agent stack",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
Launches the full ANDR stack: tool_manager, prompt_manager, task_manager,
task_brain, agent, and (optionally) the web UI.

Available tools (use with --tools):
  {available_tools}

Configuration:
  Settings are read from andr.config.yaml (auto-detected in cwd, or via
  --config). CLI flags override config file values.

Components started:
  tool_manager      C++ skill registry and dispatcher
  prompt_manager    System prompt management
  task_manager      Routes tasks to the agent
  task_brain        Priority scheduler, preemption, wander mode
  agent             LLM ReAct loop (calls tools via tool_manager)
  web UI            FastAPI + WebSocket dashboard (port 8080)

Examples:
  andr start
  andr start --backend openai --model gpt-4o
  andr start --tools speak,walk,spin
  andr start --no-ui --no-brain
  andr start --enable-wander --wander-interval 120
""",
    )
    p_start.add_argument("--config", default=None,
                         help="Path to andr.config.yaml (default: auto-detect in cwd)")
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
    p_task = sub.add_parser(
        "task",
        help="Send a task to the running agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Sends a task to the running ANDR agent via /task_manager/execute.
The agent will process the prompt using its ReAct loop and available tools.

Requires ANDR to be running (andr start).

Examples:
  andr task "Say hello"
  andr task "Walk forward 2 meters"
  andr task "Navigate to the kitchen" --context "map:home"
  andr task Check your battery level
""",
    )
    p_task.add_argument("prompt", nargs="+", help="The task prompt")
    p_task.add_argument("--context", default="", help="Optional context string")

    # --- andr status ---
    sub.add_parser(
        "status",
        help="Check which ANDR nodes are running",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Shows the status of all ANDR nodes by querying the ROS 2 node graph.

Checks for:
  agent_server      Agent (LLM ReAct loop)
  task_brain        Task Brain (scheduler/preemption)
  task_manager      Task Manager
  tool_manager      Tool Manager
  prompt_manager    Prompt Manager
  ui_server         Web UI
  + any registered tool nodes

Requires ROS 2 to be running.

Example:
  andr status
""",
    )

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
