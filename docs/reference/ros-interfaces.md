# ROS 2 Interfaces

All message types are defined in the `andr_msgs` package.

## Actions

| Server | Type | Description |
|---|---|---|
| `/task_brain/submit` | `TaskGoal` | Submit a task with priority handling |
| `/task_manager/execute` | `TaskGoal` | Direct entry point (bypasses brain) |
| `/agent/prompt` | `Agent` | LLM agent ReAct loop |
| `/tool_manager/execute` | `ExecuteSkill` | Routes to tool servers |
| `/tools/<name>` | `ExecuteSkill` | Individual tool servers |

## Services

| Service | Type | Description |
|---|---|---|
| `tool_manager/list` | `ListTools` | List registered tools |
| `tool_manager/register` | `RegisterTool` | Register a new tool |
| `agent/get_config` | `GetAgentConfig` | Get agent LLM config |
| `agent/set_config` | `SetAgentConfig` | Update agent LLM config |
| `prompt_manager/get_system_prompt` | `GetSystemPrompt` | Get system prompt |
| `prompt_manager/set_system_prompt` | `SetSystemPrompt` | Update system prompt |
| `memory_manager/store` | `StoreMemory` | Store entry in memory |
| `memory_manager/query` | `QueryMemory` | Search memory (fan-out all stores) |
| `memory_manager/list_stores` | `ListMemoryStores` | List configured stores |
| `memory_manager/status` | `GetMemoryStatus` | Status of a single store |

## Topics

| Topic | Type | Description |
|---|---|---|
| `/robot/speech` | `RobotSpeech` | Robot speech output |
| `/vision/scene` | `String` | VLM scene descriptions |

## Message imports

```python
from andr_msgs.action import ExecuteSkill, TaskGoal, Agent
from andr_msgs.srv import RegisterTool, ListTools, SaveMap
from andr_msgs.srv import StoreMemory, QueryMemory, ListMemoryStores, GetMemoryStatus
from andr_msgs.msg import Prompt, RobotSpeech
```
