# Custom Input Sources

Input sources are gateways that send tasks to the agent. The agent doesn't know or care where tasks come from.

## Basic input source

```python
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

## Lifecycle hooks

```python
def on_task_accepted(self, prompt):
    """task_manager accepted the task."""

def on_task_rejected(self, prompt):
    """task_manager rejected the task."""

def on_task_completed(self, prompt, success, summary):
    """Agent finished the task."""

def on_task_feedback(self, state, status, progress):
    """Progress update from the agent."""
```

## Busy check

Use `self.is_busy` to avoid overlapping tasks:

```python
def _on_event(self, data):
    if not self.is_busy:
        self.send_task(prompt=f"Handle: {data}")
```

## Priority hints

Set priority via the `context` field:

```python
self.send_task(
    prompt="Battery critically low. Find a charger.",
    context="priority:urgent",
)
```

The task brain recognizes `priority:urgent`, `priority:scheduled`, and `priority:idle` in the context string.

## Auto-discovery

Same as tools — drop a `.py` file into `inputs/` and `start.py` picks it up.
