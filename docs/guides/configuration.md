# Configuration

## andr.config.yaml

Created by `andr init`. This is the main config file for pip-based projects.

```yaml
# LLM settings
llm:
  backend: ollama            # ollama | openai
  model: llama3.2
  host: http://localhost:11434
  temperature: 0.2

# Agent settings
agent:
  max_iterations: 20

# Task brain
brain:
  enabled: true
  enable_wander: false
  wander_interval_sec: 60.0
  resume_preempted: true

# Scheduled tasks
scheduled_tasks:
  check_battery:
    prompt: "Check your battery level."
    interval_sec: 300

# Web UI
ui:
  enabled: true
  port: 8080

# System prompt override
# system_prompt: |
#   You are a helpful robot assistant...

# Tools from colcon workspace (optional)
# tools:
#   - speak
#   - walk
#   - spin
```

## CLI flags

All config values can be overridden via CLI flags:

```bash
andr start --backend openai --model gpt-4o
andr start --temperature 0.5 --max-iterations 30
andr start --no-ui --no-brain
andr start --enable-wander --wander-interval 30
```

See [CLI Reference](../reference/cli.md) for the full list.

## Memory configuration

The memory system stores persistent RAG knowledge. See the full [Memory Guide](memory.md) for details.

```yaml
memory:
  default_store: default
  top_k: 4
  stores:
    default:
      backend: chroma
      path: ~/.andr/memory/default
      max_size_mb: 512
      embedding_model: all-MiniLM-L6-v2
      on_full: warn          # reject | evict | warn
    # long_term:
    #   backend: chroma
    #   path: /mnt/external/memory
    #   max_size_mb: 2048
    #   on_full: evict
```

CLI flags:

```bash
andr start --memory-path ~/.andr/memory/default --memory-max-size-mb 512 --memory-on-full warn
```

## Runtime configuration (no restart)

If running from a colcon workspace, you can change settings without restarting:

```bash
# Change LLM model on the fly
ros2 service call /agent/set_config andr_msgs/srv/SetAgentConfig \
  "{llm_backend: 'openai', llm_model: 'gpt-4o'}"

# Update system prompt
ros2 service call /prompt_manager/set_system_prompt \
  andr_msgs/srv/SetSystemPrompt \
  "{prompt: 'You are a warehouse robot...'}"
```

## stack.yaml (colcon workspace)

For full colcon builds, `andr_launch/config/stack.yaml` controls which nodes to launch:

```yaml
core:
  task_brain:
    enabled: true
    params:
      enable_wander: false
  task_manager:
    enabled: true
  agent:
    enabled: true
  tool_manager:
    enabled: true
  ui:
    enabled: true
    port: "8080"

tools:
  speak:
    enabled: true
    package: andr_skills
    executable: speak_server
```

```bash
ros2 launch andr_launch stack.launch.py
```

See [Building from Source](../from-source.md) for details.
