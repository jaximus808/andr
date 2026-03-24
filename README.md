<p align="center">
  <h1 align="center">ANDR</h1>
  <p align="center">LLM agent framework for robotics, built on ROS 2</p>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> &nbsp;&bull;&nbsp;
  <a href="#custom-tools">Custom Tools</a> &nbsp;&bull;&nbsp;
  <a href="#custom-input-sources">Custom Input Sources</a> &nbsp;&bull;&nbsp;
  <a href="#architecture">Architecture</a> &nbsp;&bull;&nbsp;
  <a href="andr_core/README.md">Building from Source</a>
</p>

---

ANDR lets an LLM agent control a robot through modular, decoupled layers. Every capability the robot has is exposed as a **tool** — the agent discovers and uses them at runtime. You write tools in plain Python. No ROS knowledge required.

```
pip install andr → write a tool → python my_tool.py
```

## Quickstart

### Option A: pip (recommended for building tools)

Requires ROS 2 Humble on the host.

```bash
sudo apt install ros-humble-ros-base
source /opt/ros/humble/setup.bash
pip install andr
```

Scaffold a new project:

```bash
andr init my_robot
cd my_robot
```

This creates:

```
my_robot/
  andr.config.yaml      # LLM backend, model, agent settings
  start.py              # Launches the stack + auto-discovers your tools/inputs
  tools/
    example_tool.py     # Example BaseAgentTool — edit or replace
  inputs/
    example_input.py    # Example BaseInputSource — edit or replace
```

Edit `andr.config.yaml` to set your LLM backend, then run:

```bash
python start.py
```

Or launch directly:

```bash
andr start --model llama3.2
```

Open **http://localhost:8080** to chat with your agent.

### Option B: Docker

No ROS 2 installation required.

```bash
git clone https://github.com/jaximus808/andr.git
cd andr
docker compose up
```

Pull an LLM model (first time only):

```bash
docker exec -it andr-ollama ollama pull llama3.2
```

Open **http://localhost:8080**.

### Option C: Build from source

For contributors or full colcon workspace control. See [andr_core/README.md](andr_core/README.md).

---

## Custom Tools

A tool is a Python class that the agent can call. It auto-registers with the running stack — no config files, no rebuilds.

```python
# tools/lights.py
from andr import BaseAgentTool

class LightsTool(BaseAgentTool):
    TOOL_NAME = "lights"
    TOOL_DESCRIPTION = "Control the room lights"
    TOOL_PARAMETERS = [
        {"name": "state", "type": "string", "required": True,
         "description": "on or off"},
        {"name": "brightness", "type": "int", "required": False,
         "description": "0-100 brightness level"},
    ]

    def _execute(self, params, goal_handle):
        state = params["state"]
        brightness = params.get("brightness", 50)
        self.get_logger().info(f"Lights {state} at {brightness}%")
        return {"status": "done", "state": state}

if __name__ == "__main__":
    import rclpy
    rclpy.init()
    rclpy.spin(LightsTool())
```

```bash
python tools/lights.py
```

The agent discovers it immediately. Ask it "turn on the lights at 80% brightness" and it just works.

Drop any tool into your project's `tools/` folder and `start.py` picks it up automatically.

---

## Custom Input Sources

Input sources are gateways that send tasks to the agent. The agent doesn't know or care where tasks come from — it just receives prompts.

```python
# inputs/slack.py
from andr import BaseInputSource
from std_msgs.msg import String

class SlackInput(BaseInputSource):
    SOURCE_NAME = "slack"
    SOURCE_DESCRIPTION = "Receives tasks from Slack messages"

    def __init__(self):
        super().__init__()
        self.create_subscription(String, "/slack/messages", self._on_msg, 10)

    def _on_msg(self, msg):
        if not self.is_busy:
            self.send_task(prompt=msg.data, context="slack")

    def on_task_completed(self, prompt, success, summary):
        self.get_logger().info(f"Done: {summary}")

if __name__ == "__main__":
    import rclpy
    rclpy.init()
    rclpy.spin(SlackInput())
```

**Lifecycle hooks:** `on_task_accepted`, `on_task_rejected`, `on_task_completed`, `on_task_feedback`

---

## CLI Reference

```bash
andr init my_robot                        # Scaffold a new project
andr start                                # Start the stack with defaults
andr start --backend openai --model gpt-4o
andr start --model llama3.2 --no-ui       # Headless
andr task "Walk forward 2 meters"         # Send a task to the running agent
andr status                               # Check what nodes are running
```

| Flag | Default | Description |
|---|---|---|
| `--backend` | `ollama` | `ollama` or `openai` |
| `--model` | `llama3.2` | Model name |
| `--host` | `http://localhost:11434` | Ollama server URL |
| `--temperature` | `0.2` | Sampling temperature |
| `--max-iterations` | `20` | Agent ReAct loop cap |
| `--tools` | | Comma-separated tools to launch (e.g., `speak,walk`) |
| `--no-ui` | | Disable the web dashboard |
| `--ui-port` | `8080` | Web UI port |

---

## Architecture

```
Input Sources (Web UI, vision bridge, your custom inputs)
        │
        ▼
  task_manager          ← single entry point for all tasks
        │
        ▼
  agent_server          ← LLM ReAct loop (plan → act → observe)
        │
        ▼
  tool_manager          ← discovers and dispatches tool calls
        │
        ▼
  Tool servers          ← built-in + your custom tools
```

**Principles:**

- **Agent is tool-agnostic** — discovers capabilities at runtime, never hard-codes them
- **Everything is a tool** — any capability is a registered action server
- **Input sources are bridges** — they send tasks through task_manager, never talk to the agent directly
- **task_manager is the single entry point** — all tasks flow through it, regardless of origin

---

## Docker Configuration

| Variable | Default | Description |
|---|---|---|
| `ANDR_LLM_BACKEND` | `ollama` | `ollama` or `openai` |
| `ANDR_LLM_MODEL` | `llama3.2` | Model name |
| `ANDR_LLM_TEMPERATURE` | `0.2` | Sampling temperature |
| `ANDR_UI_PORT` | `8080` | Web UI port |
| `ANDR_TOOLS` | | Comma-separated tools to launch |
| `OPENAI_API_KEY` | | Required for `openai` backend |

GPU support (for Ollama): uncomment the `deploy` section in `docker-compose.yml`.

---

## Project Structure

```
andr/
  pip/andr/               # pip package source (pip install andr)
  andr_msgs/              # ROS 2 message/service/action definitions
  andr_core/
    agent/                # LLM agent (ReAct loop, memory, prompts)
    task_manager/         # Routes tasks → agent
    tool_manager/         # C++ skill registry + dispatcher
    andr_tools/           # Base classes: BaseAgentTool, BaseInputSource
    andr_brain/           # C++ BehaviorTree brain
    andr_launch/          # Launch files + stack.yaml config
  andr_nav/               # Navigation tools (walk, spin, navigate, map)
  andr_skills/            # Non-nav tools (speak, gesture, vision)
  andr_ui/                # Web UI (FastAPI + WebSocket)
  andr_sim/               # Gazebo simulation (URDF, worlds, Nav2)
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE) for details.
