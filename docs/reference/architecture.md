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
| **Tool servers** | Individual action servers (one per capability) |
| **Input sources** | Bridges that send tasks into the pipeline |
| **Web UI** | FastAPI + WebSocket dashboard |

## How ANDR Compares to Other Projects

The ROS 2 + LLM space has several projects, but each solves only part of the problem. ANDR is the only framework that provides a complete, reusable pipeline from input to execution.

### Existing projects

**ROSA** (NASA JPL) — A LangChain-based agent for inspecting and debugging ROS systems via natural language. Great for diagnosis, but not designed for autonomous robot control. No task pipeline, no tool registry, no input source abstraction.

**ROS-LLM** (Auromix, published in Nature Machine Intelligence) — An LLM generates executable code, behavior trees, or state machines to control a robot. Closer in ambition to ANDR, but the LLM writes raw code rather than calling tools through a registry. No input source abstraction or single entry point. Adding a new capability means changing how the LLM generates code.

**bob_llm** — A single ROS 2 node with dynamic tool loading from Python files. The closest match to ANDR's tool-calling pattern — it auto-generates tool schemas from function signatures. But it's one node, not an architecture. No task pipeline, no scheduling, no input abstraction, no separation between agent and tool dispatch.

**ROSClaw** — The most architecturally ambitious project in this space. MCP-native design that auto-translates every ROS 2 topic/service/action into JSON schemas for LLMs. Includes a digital twin firewall for safety validation. However, it's very early stage with many incomplete components and limited tests.

**CaP-X** (NVIDIA, Berkeley, Stanford, CMU) — A research benchmark for evaluating "Code-as-Policy" agents in simulation environments. Not a reusable framework — it's an evaluation suite for measuring how well LLMs generate robot control code.

### What makes ANDR different

Other projects either give you an LLM that can talk to ROS, or a way to generate robot code with an LLM. ANDR gives you a **framework for building complete LLM-driven robot systems**.

The key differences:

1. **Full pipeline architecture.** Input sources → task_brain → task_manager → agent → tool_manager → tool servers. Every layer is decoupled. No other project structures the entire flow from perception to action.

2. **Both sides abstracted.** `BaseAgentTool` standardizes the output side (tools the agent calls). `BaseInputSource` standardizes the input side (things that send tasks to the agent). No other project provides base classes for both sides of the pipeline.

3. **Agent is truly tool-agnostic.** The agent queries the tool registry at runtime and uses whatever is available. Adding a new capability — navigation, speech, vision, a custom sensor — never requires changing agent code. Most other projects either hardcode available actions or require the LLM to know about specific APIs.

4. **Single entry point with priority scheduling.** Every task flows through `task_manager` regardless of origin. The task brain handles priority queuing, preemption, and resumption. A voice command, a web UI message, a vision-triggered alert, and a scheduled check all enter through the same gate.

5. **The gap it fills.** General-purpose agent frameworks (LangChain, CrewAI, AutoGen) have sophisticated tool-calling abstractions but zero robotics awareness — no concept of action servers, sensor bridges, or physical task pipelines. Robotics frameworks (ROS 2, Nav2, MoveIt) have excellent robot control but no LLM integration pattern. ANDR bridges these two worlds: the tool-calling ergonomics of an agent framework with the real-time, multi-node architecture of ROS 2.

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
