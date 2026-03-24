# ANDR

SDK for building tools and input sources for the ANDR LLM agent framework.

## Requirements

- ROS 2 Humble: `sudo apt install ros-humble-ros-base`
- ANDR stack running (via Docker or colcon build)

## Install

```bash
source /opt/ros/humble/setup.bash
pip install andr
```

## Create a custom tool

```python
# my_tool.py — no colcon needed, just run: python my_tool.py
from andr import BaseAgentTool

class LightsTool(BaseAgentTool):
    TOOL_NAME = "lights"
    TOOL_DESCRIPTION = "Control the room lights"
    TOOL_PARAMETERS = [
        {"name": "state", "type": "string", "required": True,
         "description": "on or off"},
    ]

    def _execute(self, params, goal_handle):
        return {"status": "done", "state": params["state"]}

if __name__ == "__main__":
    import rclpy
    rclpy.init()
    rclpy.spin(LightsTool())
```

The tool auto-registers with the running ANDR tool_manager. The agent discovers it immediately.

## Create a custom input source

```python
# my_input.py
from andr import BaseInputSource
from std_msgs.msg import String

class SlackInput(BaseInputSource):
    SOURCE_NAME = "slack"
    SOURCE_DESCRIPTION = "Receives tasks from Slack"

    def __init__(self):
        super().__init__()
        self.create_subscription(String, "/slack/messages", self._on_msg, 10)

    def _on_msg(self, msg):
        if not self.is_busy:
            self.send_task(prompt=msg.data, context="slack")

if __name__ == "__main__":
    import rclpy
    rclpy.init()
    rclpy.spin(SlackInput())
```

See the [main repository](https://github.com/jaximus808/andr) for full documentation.
