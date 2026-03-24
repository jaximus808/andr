# Custom Tools

A tool is a Python class the agent can call. It auto-registers with the running stack — no config files, no rebuilds.

## Basic tool

```python
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

Run it, and the agent can immediately call it by name.

## How it works

1. Your tool starts → registers with `tool_manager`
2. Agent queries `tool_manager` for available tools before each task
3. Agent sees your tool's name, description, and parameters
4. Agent calls your tool → `tool_manager` routes to your `_execute` method
5. You return a result dict → agent decides what to do next

## Parameters

```python
TOOL_PARAMETERS = [
    {
        "name": "target",
        "type": "string",       # string, int, float, bool
        "required": True,
        "description": "What to target",
    },
    {
        "name": "speed",
        "type": "float",
        "required": False,
        "description": "Speed in m/s",
    },
]
```

Inside `_execute`, `params` is a dict:

```python
def _execute(self, params, goal_handle):
    target = params["target"]
    speed = params.get("speed", 1.0)
```

## Publishing feedback

Use `goal_handle` to send progress updates:

```python
def _execute(self, params, goal_handle):
    for i in range(10):
        # do work...
        self.publish_feedback(goal_handle, f"Step {i+1}/10", i / 10.0)
    return {"status": "done"}
```

## Auto-discovery

Drop any `.py` file into your project's `tools/` folder. `start.py` auto-discovers and launches it. Files starting with `_` are skipped.

## Running standalone

Every tool can run independently for testing:

```bash
source /opt/ros/humble/setup.bash
python tools/my_tool.py
```
