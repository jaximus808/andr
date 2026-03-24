# Building from Source

For contributors or advanced users who want the full ROS 2 colcon workspace.

!!! note
    Most users don't need this. See [Installation](getting-started/installation.md) for pip or Docker.

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
  ros-humble-nav2-navfn-planner \
  ros-humble-nav2-regulated-pure-pursuit-controller \
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

## Run

### Option A: Config-driven (recommended)

```bash
ros2 launch andr_launch stack.launch.py
```

Edit `andr_launch/config/stack.yaml` to toggle components.

### Option B: Manual (3 terminals)

```bash
# Terminal 1 — Simulation
ros2 launch andr_sim robot.launch.py

# Terminal 2 — Tools
ros2 launch andr_launch tools.launch.py

# Terminal 3 — Core
ros2 launch andr_launch andr.launch.py
```

Open [http://localhost:8080](http://localhost:8080).

## Launch arguments

| Argument | Default | Description |
|---|---|---|
| `llm_backend` | `ollama` | `ollama` or `openai` |
| `llm_model` | `llama3.2` | Model name |
| `llm_host` | `http://localhost:11434` | Ollama server URL |
| `llm_temperature` | `0.2` | Sampling temperature |
| `max_iterations` | `20` | Agent loop cap |
| `launch_vision` | `false` | Enable VLM vision tool |
| `ui_port` | `8080` | Web UI port |

```bash
ros2 launch andr_launch andr.launch.py llm_backend:=openai llm_model:=gpt-4o
```

## Simulation

```bash
# Mapping mode (SLAM)
ros2 launch andr_sim robot.launch.py

# Localization mode (saved map)
ros2 launch andr_sim robot.launch.py localization:=true \
  map_file:=$HOME/andr_maps/my_map
```
