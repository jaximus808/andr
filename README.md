# ANDR — Robot Stack

A ROS 2 (Humble) robot stack with an autonomous LLM agent (LangChain + LangGraph), skill execution pipeline, task manager, and a live web dashboard.

---

## Current State

- **Brain**: C++ BehaviorTree node with an idle wander loop that picks random skills. Can be disabled at launch with `enable_wander:=false`.
- **Agent**: LangChain ReAct agent (via `langgraph`) served as a ROS 2 action server. Connects to Ollama (local) or OpenAI. Tools are auto-generated from `skills.yaml`.
- **Task Manager**: Bridges the web UI to the agent — accepts prompts, forwards to `/agent/prompt`, relays results back.
- **Skill Executor**: C++ config-driven router that dispatches `ExecuteSkill` actions to the correct hardware skill server based on `skill_executor_config.yaml`. Currently routes `speak` and `walk`.
- **Robot Skills**: Mock action servers for `speak` (TTS) and `walk`. Map management service for saving/loading SLAM maps. Placeholder for real hardware drivers.
- **Map Manager**: Service node that saves the current SLAM occupancy grid + pose graph to disk, lists saved maps, and supports switching to localization mode via launch args.
- **Web UI**: FastAPI + WebSocket dashboard at `http://localhost:8080` — chat interface, event log, and system status panel showing active nodes/action servers.

---

## Package Overview

| Package | Type | Description |
|---|---|---|
| `andr` | `ament_cmake` | Core C++ brain, BehaviorTree, action/message types, configs, launch |
| `agent` | `ament_python` | LLM agent action server (LangGraph ReAct agent on `/agent/prompt`) |
| `task_manager` | `ament_python` | Task bridge — receives tasks from UI, forwards to agent, relays results |
| `skill_executor` | `ament_cmake` | C++ action server that dispatches robot skills based on YAML config |
| `robot_skills` | `ament_python` | Mock hardware-interface action servers (speak, walk) + map management service |
| `andr_ui` | `ament_python` | FastAPI web dashboard (event log, chat, system status panel) |

---

## Prerequisites

### System
```bash
# ROS 2 Humble
source /opt/ros/humble/setup.bash

# BehaviorTree.CPP v3
sudo apt install ros-humble-behaviortree-cpp-v3

# yaml-cpp
sudo apt install libyaml-cpp-dev

# Nav2 (navigation stack)
sudo apt install ros-humble-nav2-bringup ros-humble-nav2-bt-navigator \
  ros-humble-nav2-controller ros-humble-nav2-planner ros-humble-nav2-behaviors \
  ros-humble-nav2-waypoint-follower ros-humble-nav2-velocity-smoother \
  ros-humble-nav2-smoother ros-humble-nav2-lifecycle-manager \
  ros-humble-nav2-navfn-planner ros-humble-nav2-regulated-pure-pursuit-controller \
  ros-humble-nav2-costmap-2d
```

### Python dependencies
```bash
# Agent (LangChain + Ollama)
pip install langchain langchain-core langchain-community langchain-ollama langchain-openai \
            langgraph pyyaml chromadb sentence-transformers

# Web UI
pip install fastapi "uvicorn[standard]" websockets
```

### Ollama (local LLM)
```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull a model (pick one)
ollama pull llama3.2
ollama pull qwen2.5
```

---

## Build

```bash
cd ~/andr

# Build message/action types first (other packages depend on them)
colcon build --packages-select andr

# Build everything else
colcon build --packages-select agent task_manager skill_executor robot_skills andr_ui

# Or build everything in one shot (colcon resolves order automatically)
colcon build

# Source the workspace
source install/setup.bash
```

> **Tip:** use `--symlink-install` during development so Python changes take effect without rebuilding:
> ```bash
> colcon build --symlink-install
> ```

---

## Run

### Full stack
```bash
source install/setup.bash
ros2 launch andr andr.launch.py
```

Then open **http://localhost:8080** in a browser for the web dashboard.

---

### Launch arguments

| Argument | Default | Description |
|---|---|---|
| `launch_brain` | `true` | Start the `andr_brain` C++ node |
| `enable_wander` | `true` | Enable the BT wander loop inside the brain (set `false` to keep brain alive without autonomous wandering) |
| `launch_agent` | `true` | Start the `agent_server` Python node |
| `launch_task_mgr` | `true` | Start the `task_manager_server` Python node |
| `launch_skills` | `true` | Start `skill_executor` + mock skill servers |
| `launch_ui` | `true` | Start the web dashboard |
| `ui_port` | `8080` | Port for the web UI |
| `log_level` | `info` | ROS log level (`debug`/`info`/`warn`/`error`) |
| `llm_backend` | `ollama` | `ollama` or `openai` |
| `llm_model` | `llama3.2` | Model name (e.g. `llama3.2`, `qwen2.5`, `gpt-4o`) |
| `llm_host` | `http://localhost:11434` | Ollama server URL |
| `llm_temperature` | `0.2` | Sampling temperature |
| `memory_backend` | `chroma` | RAG backend: `chroma` |
| `memory_top_k` | `4` | Number of RAG results to inject |
| `skills_yaml` | *(installed share)* | Path to `skills.yaml` |
| `max_iterations` | `20` | Agent loop iteration cap |

---

### Common launch variants

```bash
# Default (Ollama + llama3.2)
ros2 launch andr andr.launch.py

# Disable autonomous wander (brain stays up for /incoming_task)
ros2 launch andr andr.launch.py enable_wander:=false

# Use a different model
ros2 launch andr andr.launch.py llm_model:=qwen2.5

# OpenAI backend (requires OPENAI_API_KEY env var)
ros2 launch andr andr.launch.py llm_backend:=openai llm_model:=gpt-4o

# UI only (no brain, agent, or skills — for UI development)
ros2 launch andr andr.launch.py launch_brain:=false launch_agent:=false launch_skills:=false launch_task_mgr:=false

# Agent + task_manager + UI only (no brain or skills)
ros2 launch andr andr.launch.py launch_brain:=false launch_skills:=false

# Custom UI port
ros2 launch andr andr.launch.py ui_port:=9000

# Verbose logging
ros2 launch andr andr.launch.py log_level:=debug
```

---

## Simulation

### Launch the simulation

```bash
source install/setup.bash

# Default — mapping mode (SLAM builds a new map)
ros2 launch andr_sim robot.launch.py

# Localization mode — load a previously saved map
ros2 launch andr_sim robot.launch.py localization:=true map_file:=$HOME/andr_maps/my_map
```

### Simulation launch arguments

| Argument | Default | Description |
|---|---|---|
| `use_sim_time` | `true` | Use Gazebo simulation clock |
| `world` | `test_world.world` | Path to Gazebo world file |
| `localization` | `false` | Run SLAM Toolbox in localization mode instead of mapping |
| `map_file` | *(empty)* | Path to serialized map for localization (without file extension) |

---

## Map Management

The `map_server` node starts automatically with the simulation and provides services for saving and retrieving SLAM maps. Saved maps are stored in `~/andr_maps/` by default.

### Services

| Service | Type | Description |
|---|---|---|
| `/map_manager/save_map` | `andr/srv/SaveMap` | Save the current occupancy grid + SLAM pose graph to disk |
| `/map_manager/get_maps` | `andr/srv/GetMaps` | List all saved map names |

### Save a map

```bash
ros2 service call /map_manager/save_map andr/srv/SaveMap "{map_name: 'my_map'}"
```

This saves to `~/andr_maps/`:
- `my_map.pgm` + `my_map.yaml` — standard occupancy grid (viewable in any image viewer)
- `my_map.posegraph` + `my_map.data` — SLAM Toolbox pose graph (needed for localization)

If the name already exists, the files are overwritten.

### List saved maps

```bash
ros2 service call /map_manager/get_maps andr/srv/GetMaps
```

### Localize on a saved map

Once you have a saved map, launch the simulation in localization mode:

```bash
ros2 launch andr_sim robot.launch.py localization:=true map_file:=$HOME/andr_maps/my_map
```

This launches `localization_slam_toolbox_node` instead of the mapping node, loading the serialized pose graph so the robot localizes against the existing map.

---

## Run nodes individually

```bash
source install/setup.bash

# Agent server (LangChain agent on /agent/prompt)
ros2 run agent agent_server

# Task manager (bridges UI -> agent on /task_manager/execute)
ros2 run task_manager task_manager_server

# Skill executor (C++ router on /skill_executor)
ros2 run skill_executor skill_executor_node

# Mock skill servers
ros2 run robot_skills speak_server
ros2 run robot_skills walk_server

# Map management service
ros2 run robot_skills map_server

# Web UI server
ros2 run andr_ui ui_server
# or with a custom port:
ANDR_UI_PORT=9000 ros2 run andr_ui ui_server
```

---

## Architecture

```
Browser (http://localhost:8080)
  |  WebSocket
  v
andr_ui (FastAPI + ROS bridge)
  |  /task_manager/execute (TaskGoal action)
  v
task_manager_server
  |  /agent/prompt (Agent action)
  v
agent_server (LangGraph ReAct agent)
  |  Tools call SkillExecutor
  v
skill_executor (C++ router)
  |  /skills/speak, /skills/walk (ExecuteSkill action)
  v
robot_skills (individual action servers)
```

---

## Action servers

| Server | Type | Description |
|---|---|---|
| `/task_manager/execute` | `andr/action/TaskGoal` | UI sends tasks here; relayed to agent |
| `/agent/prompt` | `andr/action/Agent` | LangChain agent runs autonomously |
| `/skill_executor` | `andr/action/ExecuteSkill` | Routes skills to hardware servers |
| `/skills/speak` | `andr/action/ExecuteSkill` | TTS mock server |
| `/skills/walk` | `andr/action/ExecuteSkill` | Walking mock server |
| `/wander` | `andr/action/Wander` | Wander behavior |

## Services

| Service | Type | Description |
|---|---|---|
| `/map_manager/save_map` | `andr/srv/SaveMap` | Save current SLAM map to disk |
| `/map_manager/get_maps` | `andr/srv/GetMaps` | List all saved maps |

## Key topics

| Topic | Type | Direction | Description |
|---|---|---|---|
| `/ui/prompt` | `andr/msg/Prompt` | UI -> ROS | Prompt published for logging |
| `/robot/speech` | `andr/msg/RobotSpeech` | ROS -> UI | Robot speech in chat panel |
| `/agent/feedback` | `std_msgs/String` (JSON) | ROS -> UI | Agent loop state updates |
| `/robot/status` | `std_msgs/String` | ROS -> UI | Arbitrary status strings |

---

## Send a task from the CLI (without the UI)

```bash
# Via task_manager
ros2 action send_goal --feedback /task_manager/execute andr/action/TaskGoal \
  "{prompt: 'Go to the kitchen and look for a cup', context: ''}"

# Directly to the agent
ros2 action send_goal --feedback /agent/prompt andr/action/Agent \
  "{prompt: 'Navigate to the living room', context: ''}"
```

---

## Manually publish to UI topics (for testing)

```bash
# Simulate robot speaking
ros2 topic pub --once /robot/speech andr/msg/RobotSpeech \
  "{text: 'I have arrived at the kitchen', emotion: 'happy'}"

# Simulate a status update
ros2 topic pub --once /robot/status std_msgs/msg/String \
  "{data: 'Battery at 80%'}"
```

---

## Rebuild after changes

```bash
cd ~/andr

# Message/action type changes (always rebuild andr first, then dependents)
colcon build --packages-select andr
colcon build --packages-select agent task_manager skill_executor robot_skills andr_ui
source install/setup.bash

# Python-only changes (with --symlink-install this is instant)
colcon build --packages-select agent task_manager andr_ui
source install/setup.bash

# C++ changes
colcon build --packages-select andr skill_executor
source install/setup.bash
```
