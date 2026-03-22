# ANDR — Claude Development Guide

## What is ANDR?

ANDR is a ROS 2 robotics toolkit where an LLM agent controls a robot through
modular, decoupled layers. Every capability the robot has is exposed as a **tool**
registered with the tool_manager. The agent is just one consumer of the task pipeline.

## Architecture — Strict Layer Separation

```
┌─────────────────────────────────────────────────────────┐
│  INPUT SOURCES (any number, all equivalent)              │
│  ┌──────────┐  ┌──────────────────┐  ┌───────────────┐  │
│  │  Web UI  │  │ VisionTaskBridge │  │  Future: Audio │  │
│  └────┬─────┘  └────────┬─────────┘  └───────┬───────┘  │
│       │                 │                     │          │
│       └────────┬────────┴─────────────────────┘          │
│                ▼                                         │
│  ┌─────────────────────┐                                 │
│  │    task_manager      │  /task_manager/execute          │
│  │  (single entry point)│  All tasks flow through here   │
│  └──────────┬──────────┘                                 │
│             ▼                                            │
│  ┌─────────────────────┐                                 │
│  │    agent_server      │  /agent/prompt                  │
│  │  (LLM ReAct loop)   │  Receives prompt, calls tools   │
│  └──────────┬──────────┘                                 │
│             ▼                                            │
│  ┌─────────────────────┐                                 │
│  │    tool_manager      │  C++ router                     │
│  │  (skill registry)   │  Dispatches to skill servers    │
│  └──────────┬──────────┘                                 │
│             ▼                                            │
│  ┌─────────────────────┐                                 │
│  │    robot_skills      │  speak, walk, gesture, etc.     │
│  │  (action servers)   │  Each is an independent node    │
│  └─────────────────────┘                                 │
└─────────────────────────────────────────────────────────┘
```

## Core Principles — READ BEFORE MAKING CHANGES

### 1. The agent is tool-agnostic
The agent does NOT know about specific capabilities (vision, audio, navigation).
It receives a prompt via the task pipeline and uses whatever tools the tool_manager
exposes. Never add sensor-specific subscriptions, imports, or logic to agent.py.

### 2. Everything is a tool
Any capability the robot has must be registered as a tool in tool_manager.
The agent discovers tools dynamically at startup and before each execution.
To add a new capability: create a skill action server, register it with
tool_manager — the agent will find it automatically.

### 3. Input sources are bridges, not agents
Vision, audio, or any future sensor should follow this pattern:
- A **perception node** processes raw data (e.g., VisionNode runs a VLM on camera frames)
- A **bridge node** watches the perception output and sends tasks through `/task_manager/execute`
- The bridge is the ONLY thing that knows about the sensor — the agent never does

Example: VisionNode → /vision/scene → VisionTaskBridge → /task_manager/execute → agent

### 4. The task_manager is the single entry point
ALL tasks reach the agent through task_manager, whether from the UI, a sensor
bridge, or a future API. Never bypass task_manager by sending directly to
/agent/prompt from new input sources.

### 5. System prompt stays generic
The system prompt (agent/agent/prompts/system_prompt.py) describes the robot's
identity and general behavior. It must NOT contain instructions for specific
sensors or tools. Tool descriptions come from the tool registry at runtime.

## Package Overview

| Package | Language | Role |
|---|---|---|
| `andr` | C++ | Core: messages, actions, services, launch files, behavior tree |
| `agent` | Python | LLM agent with ReAct loop, memory, prompt management |
| `task_manager` | Python | Routes tasks from any input source to the agent |
| `tool_manager` | C++ | Discovers and dispatches tool calls to skill servers |
| `robot_skills` | Python | Individual tool servers (speak, walk, vision, gesture, etc.) |
| `andr_ui` | Python | Web UI (FastAPI + WebSocket bridge to ROS) |
| `prompt_manager` | Python | System prompt versioning and serving |

## Base Classes (andr_tools package)

The `andr_tools` package provides two base classes that standardize both sides
of the agent pipeline:

### BaseAgentTool — output side (tools the agent can call)
Subclass to create a tool that auto-registers with tool_manager.
Override `_execute(params, goal_handle)` with your tool logic.

```python
from andr_tools import BaseAgentTool

class SpeakTool(BaseAgentTool):
    TOOL_NAME = "speak"
    TOOL_DESCRIPTION = "Text-to-speech via robot speaker"
    TOOL_PARAMETERS = [{"name": "text", "type": "string", "required": True, ...}]

    def _execute(self, params, goal_handle):
        return {"status": "done"}
```

### BaseInputSource — input side (things that send tasks to the agent)
Subclass to create an input source that sends tasks through the task_manager
pipeline. Override `send_task(prompt, context)` is your main API. Override
the `on_task_*` hooks for lifecycle events.

```python
from andr_tools import BaseInputSource

class SmsInput(BaseInputSource):
    SOURCE_NAME = "sms"
    SOURCE_DESCRIPTION = "Receives tasks via text message"

    def __init__(self):
        super().__init__()
        # set up your subscription / listener here
        self.create_subscription(String, "/sms/incoming", self._on_sms, 10)

    def _on_sms(self, msg):
        self.send_task(prompt=msg.data, context="sms_message")
```

Available hooks: `on_task_accepted`, `on_task_rejected`, `on_task_completed`,
`on_task_feedback`. The `is_busy` property tells you if a task is in-flight.

## Adding a New Capability (tool)

1. Create a new action server in `robot_skills/` subclassing `BaseAgentTool`
2. Register the entry point in `robot_skills/setup.py`
3. Add the node to the appropriate launch file in `andr/launch/`
4. The tool auto-registers with tool_manager — the agent discovers it at runtime

Do NOT modify agent.py, the system prompt, or any agent code to support new tools.

## Adding a New Input Source (sensor, API, scheduled task, etc.)

1. Create a perception node if needed (processes raw data into structured output)
2. Create a bridge node subclassing `BaseInputSource` from `andr_tools`
3. In the bridge, call `self.send_task(prompt, context)` — it handles the full
   task_manager lifecycle (send, feedback, result)
4. Register the entry point and add to the launch file

Do NOT subscribe to sensor topics from agent.py. Do NOT inject sensor data
into the agent's prompt directly. Always go through `BaseInputSource.send_task()`.

## Build & Run

```bash
cd /home/user/andr
colcon build --symlink-install
source install/setup.bash

# Launch everything
ros2 launch andr tools.launch.py launch_vision:=true
ros2 launch andr andr.launch.py

# Or individual nodes
ros2 run robot_skills speak_server
ros2 run agent agent
```

## Key ROS Interfaces

| Interface | Type | Path |
|---|---|---|
| Task entry | Action | `/task_manager/execute` (TaskGoal) |
| Agent prompt | Action | `/agent/prompt` (Agent) |
| Tool execution | Action | `/tools/<name>` (ExecuteSkill) |
| Tool listing | Service | `tool_manager/list` |
| System prompt | Service | `prompt_manager/get_system_prompt` |
| Vision scene | Topic | `/vision/scene` (std_msgs/String) |
