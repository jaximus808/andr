# Task Brain

The task brain is a priority-based scheduler that sits above the task manager. It handles queuing, preemption, scheduled tasks, and optional idle behavior.

## Priority levels

| Priority | Level | Source |
|---|---|---|
| **URGENT** | 4 | Safety alerts, critical events (`context="priority:urgent"`) |
| **USER** | 3 | Web UI, CLI, input sources (default) |
| **SCHEDULED** | 2 | Recurring cron-like tasks |
| **IDLE** | 1 | Wander behavior |

Higher-priority tasks preempt lower-priority ones.

## Preemption and resume

When a higher-priority task arrives while a lower-priority one is running:

1. The brain cancels the running task
2. The agent's latest progress is saved as text (the feedback state)
3. The new task runs to completion
4. The brain resumes the interrupted task with context: *"You were previously doing X. Continue."*

State is just text — the agent saves its own progress by describing it. Resume is a new prompt with that context injected.

!!! note
    Idle (wander) tasks are never resumed. Only USER and above get saved.

## Configuration

In `andr.config.yaml`:

```yaml
brain:
  enabled: true
  enable_wander: false
  wander_interval_sec: 60.0
  resume_preempted: true
  # wander_prompt: "Look around and describe what you see."
```

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | Enable the task brain |
| `enable_wander` | `false` | Send idle prompts when no tasks pending |
| `wander_interval_sec` | `60.0` | Seconds between wander prompts |
| `resume_preempted` | `true` | Resume interrupted tasks after preemption |
| `wander_prompt` | *(rotates defaults)* | Custom wander prompt |

## CLI flags

```bash
andr start --enable-wander                  # turn on idle behavior
andr start --enable-wander --wander-interval 30
andr start --no-brain                       # disable brain entirely
andr start --no-resume                      # don't resume interrupted tasks
```

## Scheduled tasks

Define recurring tasks in `andr.config.yaml`:

```yaml
scheduled_tasks:
  check_battery:
    prompt: "Check your battery level and report it."
    interval_sec: 300
  patrol:
    prompt: "Do a patrol of the area and report anything unusual."
    interval_sec: 600
```

Scheduled tasks run at `SCHEDULED` priority — they preempt wander but not user tasks.

## Wander mode

When enabled and the queue is empty, the brain sends idle prompts like:

- *"You're idle. Look around and describe what you observe."*
- *"Nothing is happening. Check your surroundings."*
- *"Idle time. Wander around and see if anything needs attention."*

These rotate automatically, or you can set a custom prompt in the config.

Any real task (user, scheduled, or urgent) immediately preempts wander.

## How tasks flow

```
Input Sources / UI / Scheduled tasks
        │
        ▼
  task_brain            ← priority queue + preemption logic
        │
        ▼
  task_manager          ← forwards to agent, relays feedback
        │
        ▼
  agent_server          ← LLM ReAct loop
```
