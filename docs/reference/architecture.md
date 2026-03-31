# Architecture

## Data flow

```
Input Sources (Web UI, vision bridge, your custom inputs)
        │
        ▼
  task_brain            ← priority queue, preemption, scheduling, wander
        │
        ▼
  task_manager          ← single entry point, forwards to agent
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

## Principles

### Agent is tool-agnostic

The agent doesn't know about specific capabilities. It queries `tool_manager` for available tools at runtime and uses whatever is registered. Adding a new tool never requires changing agent code.

### Everything is a tool

Any capability the robot has is registered as a tool action server. The agent discovers them dynamically. To add a capability: create a tool, run it, done.

### Input sources are bridges

Vision, audio, Slack, or any sensor follows the same pattern: observe something → send a task through `task_manager`. Input sources never talk to the agent directly.

### task_manager is the single entry point

All tasks flow through `task_manager`, regardless of origin. This ensures consistent queuing, priority handling, and feedback.

## Components

| Component | Role |
|---|---|
| **task_brain** | Priority queue, preemption, scheduled tasks, wander mode |
| **task_manager** | Receives tasks, forwards to agent, relays feedback |
| **agent_server** | LLM ReAct loop — reasons, calls tools, produces answers |
| **tool_manager** | C++ registry — discovers, registers, and routes tool calls |
| **prompt_manager** | Manages versioned system prompts |
| **memory_manager** | Multi-store RAG memory (ChromaDB), size management |
| **Tool servers** | Individual action servers (one per capability) |
| **Input sources** | Bridges that send tasks into the pipeline |
| **Web UI** | FastAPI + WebSocket dashboard |

## Project structure

```
andr/
  pip/andr/               # pip package source (pip install andr)
  andr_msgs/              # ROS 2 message/service/action definitions
  andr_core/
    agent/                # LLM agent (ReAct loop, memory, prompts)
    task_manager/         # Task manager + task brain
    tool_manager/         # C++ skill registry + dispatcher
    andr_tools/           # Base classes: BaseAgentTool, BaseInputSource
    andr_brain/           # Legacy C++ BehaviorTree brain
    andr_launch/          # Launch files + stack.yaml config
  andr_nav/               # Navigation tools (walk, spin, navigate, map)
  andr_skills/            # Non-nav tools (speak, gesture, vision)
  andr_ui/                # Web UI (FastAPI + WebSocket)
  andr_sim/               # Gazebo simulation (URDF, worlds, Nav2)
```
