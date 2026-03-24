# ANDR Core — Building from Source

This guide is for contributors or advanced users who want to build and run the full ANDR stack from source using ROS 2 colcon.

> **Most users don't need this.** See the [root README](../README.md) for `pip install andr` or Docker quickstart.

---

## Prerequisites

```bash
# ROS 2 Humble
source /opt/ros/humble/setup.bash

# System packages
sudo apt install ros-humble-behaviortree-cpp-v3 libyaml-cpp-dev

# Nav2 stack (for navigation tools)
sudo apt install ros-humble-nav2-bringup ros-humble-nav2-bt-navigator \
  ros-humble-nav2-controller ros-humble-nav2-planner ros-humble-nav2-behaviors \
  ros-humble-nav2-waypoint-follower ros-humble-nav2-velocity-smoother \
  ros-humble-nav2-smoother ros-humble-nav2-lifecycle-manager \
  ros-humble-nav2-navfn-planner ros-humble-nav2-regulated-pure-pursuit-controller \
  ros-humble-nav2-costmap-2d

# Python dependencies
pip install langchain langchain-core langchain-ollama langchain-openai \
            pyyaml chromadb sentence-transformers pydantic \
            fastapi "uvicorn[standard]" websockets

# Ollama (local LLM)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2
```

## Build

```bash
cd ~/andr
colcon build --symlink-install
source install/setup.bash
```

## Run (3 terminals)

Start in order — sim first, then tools, then core.

```bash
# Terminal 1 — Simulation (Gazebo + Nav2 + SLAM)
ros2 launch andr_sim robot.launch.py

# Terminal 2 — Tools (tool_manager + all skill servers)
ros2 launch andr_launch tools.launch.py

# Terminal 3 — Core (brain + agent + task_manager + prompt_manager + UI)
ros2 launch andr_launch andr.launch.py
```

Or use the config-driven launcher:

```bash
ros2 launch andr_launch stack.launch.py
```

Open **http://localhost:8080** for the web dashboard.

## Launch Arguments

| Argument | Default | Description |
|---|---|---|
| `launch_brain` | `true` | Start the BehaviorTree brain node |
| `enable_wander` | `true` | Enable autonomous wander loop |
| `launch_agent` | `true` | Start the LLM agent |
| `launch_task_mgr` | `true` | Start the task manager |
| `launch_ui` | `true` | Start the web dashboard |
| `ui_port` | `8080` | Web UI port |
| `llm_backend` | `ollama` | `ollama` or `openai` |
| `llm_model` | `llama3.2` | Model name |
| `llm_host` | `http://localhost:11434` | Ollama server URL |
| `llm_temperature` | `0.2` | Sampling temperature |
| `max_iterations` | `20` | Agent loop cap |
| `launch_vision` | `false` | Enable VLM vision tool (tools.launch.py) |

## Common Variants

```bash
# OpenAI backend
ros2 launch andr_launch andr.launch.py llm_backend:=openai llm_model:=gpt-4o

# Disable wander
ros2 launch andr_launch andr.launch.py enable_wander:=false

# With vision
ros2 launch andr_launch tools.launch.py launch_vision:=true
```

## stack.yaml (Config-Driven Launch)

Edit `andr_launch/config/stack.yaml` to toggle components:

```yaml
llm:
  backend: ollama
  model: llama3.2
  host: http://localhost:11434
  temperature: 0.2

core:
  brain:
    enabled: true
    params:
      enable_wander: false
  task_manager:
    enabled: true
  agent:
    enabled: true
  tool_manager:
    enabled: true
  ui:
    enabled: true
    port: "8080"

tools:
  speak:
    enabled: true
    package: andr_skills
    executable: speak_server
  walk:
    enabled: true
    package: andr_nav
    executable: walk_server

inputs:
  vision_bridge:
    enabled: false
    package: andr_skills
    executable: vision_task_bridge
```

## Runtime Configuration (No Restart)

```bash
# Change LLM model on the fly
ros2 service call /agent/set_config andr_msgs/srv/SetAgentConfig \
  "{llm_backend: 'openai', llm_model: 'gpt-4o'}"

# Update system prompt
ros2 service call /prompt_manager/set_system_prompt andr_msgs/srv/SetSystemPrompt \
  "{prompt: 'You are a helpful warehouse robot...'}"
```

## Simulation

```bash
# Mapping mode (SLAM builds a new map)
ros2 launch andr_sim robot.launch.py

# Localization mode (use a saved map)
ros2 launch andr_sim robot.launch.py localization:=true map_file:=$HOME/andr_maps/my_map
```

### Map Management

```bash
ros2 service call /map_manager/save_map andr_msgs/srv/SaveMap "{map_name: 'kitchen'}"
ros2 service call /map_manager/get_maps andr_msgs/srv/GetMaps
```

## ROS 2 Interfaces

### Actions

| Server | Type | Description |
|---|---|---|
| `/task_manager/execute` | `TaskGoal` | Entry point for all tasks |
| `/agent/prompt` | `Agent` | LLM agent ReAct loop |
| `/tool_manager/execute` | `ExecuteSkill` | Routes to tool servers |
| `/tools/<name>` | `ExecuteSkill` | Individual tool servers |

### Services

| Service | Type | Description |
|---|---|---|
| `tool_manager/list` | `ListTools` | List registered tools |
| `tool_manager/register` | `RegisterTool` | Register a new tool |
| `agent/get_config` | `GetAgentConfig` | Get agent LLM config |
| `agent/set_config` | `SetAgentConfig` | Update agent LLM config |
| `prompt_manager/get_system_prompt` | `GetSystemPrompt` | Get system prompt |
| `prompt_manager/set_system_prompt` | `SetSystemPrompt` | Update system prompt |

### Topics

| Topic | Type | Description |
|---|---|---|
| `/robot/speech` | `RobotSpeech` | Robot speech output |
| `/vision/scene` | `String` | VLM scene descriptions |

All message types are in the `andr_msgs` package.

## Package Structure

| Package | Language | Role |
|---|---|---|
| `andr_msgs` | C++ (rosidl) | Message, service, and action definitions |
| `agent` | Python | LLM agent with ReAct loop, memory, prompts |
| `task_manager` | Python | Routes tasks from any input source to the agent |
| `tool_manager` | C++ | Discovers and dispatches tool calls to skill servers |
| `andr_tools` | Python | Base classes: BaseAgentTool, BaseInputSource |
| `andr_brain` | C++ | BehaviorTree brain, wander planner |
| `andr_launch` | Python | Launch files and stack.yaml config |
| `andr_nav` | Python | Navigation tools (walk, spin, navigate, map) |
| `andr_skills` | Python | Non-nav tools (speak, gesture, vision) |
| `andr_ui` | Python | Web UI (FastAPI + WebSocket) |
| `andr_sim` | C++ | Gazebo simulation (URDF, worlds, Nav2 config) |
