# ANDR

**LLM agent framework for robotics, built on ROS 2.**

ANDR lets an LLM agent control a robot through modular, decoupled layers. Every capability the robot has is exposed as a **tool** — the agent discovers and uses them at runtime. You write tools in plain Python. No ROS knowledge required.

```
pip install andr → write a tool → python my_tool.py
```

## Why ANDR?

- **Tool-agnostic agent** — the agent discovers what it can do at runtime. Add a tool, and it's immediately available.
- **Write tools in plain Python** — subclass `BaseAgentTool`, define parameters, implement `_execute`. No ROS boilerplate.
- **Priority-based scheduling** — user tasks preempt idle behavior, urgent tasks preempt everything. Interrupted tasks resume automatically.
- **Any LLM backend** — Ollama (local) or OpenAI out of the box. Swap models with a config change.
- **Pluggable inputs** — UI, vision, Slack, scheduled tasks, or anything else. All go through the same pipeline.

## Quick Example

```python
from andr import BaseAgentTool

class LightsTool(BaseAgentTool):
    TOOL_NAME = "lights"
    TOOL_DESCRIPTION = "Control the room lights"
    TOOL_PARAMETERS = [
        {"name": "state", "type": "string", "required": True,
         "description": "on or off"},
    ]

    def _execute(self, params, goal_handle):
        self.get_logger().info(f"Lights {params['state']}")
        return {"status": "done"}

if __name__ == "__main__":
    import rclpy
    rclpy.init()
    rclpy.spin(LightsTool())
```

The agent discovers it immediately. Ask "turn on the lights" and it calls your tool.

## Next Steps

- [Installation](getting-started/installation.md) — pip, Docker, or from source
- [Your First Project](getting-started/first-project.md) — scaffold and run a project in 2 minutes
- [Custom Tools](guides/tools.md) — full guide to building tools
- [Architecture](reference/architecture.md) — how the pieces fit together
