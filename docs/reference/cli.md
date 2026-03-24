# CLI Reference

## andr init

Scaffold a new project.

```bash
andr init my_robot
```

Creates a project directory with `andr.config.yaml`, `start.py`, `tools/`, and `inputs/`.

## andr start

Start the ANDR agent stack.

```bash
andr start [flags]
```

| Flag | Default | Description |
|---|---|---|
| `--backend` | `ollama` | `ollama` or `openai` |
| `--model` | `llama3.2` | Model name |
| `--host` | `http://localhost:11434` | Ollama server URL |
| `--temperature` | `0.2` | Sampling temperature |
| `--max-iterations` | `20` | Agent ReAct loop cap |
| `--tools` | | Comma-separated tools (e.g., `speak,walk`) |
| `--no-ui` | | Disable the web dashboard |
| `--ui-port` | `8080` | Web UI port |
| `--no-brain` | | Disable task brain |
| `--enable-wander` | | Enable idle behavior |
| `--wander-interval` | `60` | Seconds between idle prompts |
| `--no-resume` | | Don't resume interrupted tasks |

Examples:

```bash
andr start --model llama3.2
andr start --backend openai --model gpt-4o
andr start --enable-wander --no-ui
andr start --no-brain --tools speak,walk
```

## andr task

Send a task to the running agent.

```bash
andr task "Walk forward 2 meters"
andr task "Say hello" --context "priority:urgent"
```

| Flag | Default | Description |
|---|---|---|
| `--context` | `""` | Optional context string |

## andr status

Check which ANDR nodes are running.

```bash
andr status
```

Shows the status of core nodes (agent, task_brain, task_manager, tool_manager, prompt_manager, UI) and any registered tool nodes.
