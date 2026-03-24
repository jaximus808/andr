# Your First Project

## Scaffold

```bash
andr init my_robot
cd my_robot
```

This creates:

```
my_robot/
  andr.config.yaml      # LLM backend, model, agent settings
  start.py              # Launches the stack + auto-discovers your tools/inputs
  tools/
    example_tool.py     # Example BaseAgentTool — edit or replace
  inputs/
    example_input.py    # Example BaseInputSource — edit or replace
```

## Configure

Edit `andr.config.yaml` to set your LLM:

```yaml
llm:
  backend: ollama
  model: llama3.2
  host: http://localhost:11434
```

## Run

```bash
python start.py
```

Or launch directly without a project:

```bash
andr start --model llama3.2
```

Open [http://localhost:8080](http://localhost:8080) to chat with your agent.

## Send a task from the CLI

```bash
andr task "Say hello and introduce yourself"
```

## Check what's running

```bash
andr status
```

## Add your own tool

Create a file in `tools/`:

```python
# tools/greeter.py
from andr import BaseAgentTool

class GreeterTool(BaseAgentTool):
    TOOL_NAME = "greet"
    TOOL_DESCRIPTION = "Greet someone by name"
    TOOL_PARAMETERS = [
        {"name": "name", "type": "string", "required": True,
         "description": "Who to greet"},
    ]

    def _execute(self, params, goal_handle):
        name = params["name"]
        self.get_logger().info(f"Hello, {name}!")
        return {"status": "done", "greeting": f"Hello, {name}!"}

def main(args=None):
    import rclpy
    rclpy.init(args=args)
    rclpy.spin(GreeterTool())

if __name__ == "__main__":
    main()
```

Restart `start.py` — it auto-discovers the new file. Ask the agent *"greet Alice"* and it calls your tool.

## Next steps

- [Custom Tools](../guides/tools.md) — parameters, feedback, execution model
- [Custom Input Sources](../guides/inputs.md) — feed tasks from any source
- [Task Brain](../guides/task-brain.md) — scheduling, preemption, wander mode
