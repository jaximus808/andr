# Refactor: Tool Executor → Tool Manager + BaseAgentTool

## Overview

Replace the static, config-driven C++ `skill_executor` with a **dynamic C++ Tool Manager** that accepts runtime registration from tools, and create a **Python `BaseAgentTool`** base class that tools inherit from to auto-register/deregister. Two new packages:

1. **`tool_manager/`** (C++ ament_cmake) — the central registry + router node
2. **`andr_tools/`** (Python ament_python) — `BaseAgentTool` base class for tool authors

---

## Step 1: Define new ROS2 service interfaces

Add to **`andr/srv/`**:

### `RegisterTool.srv`
```
string tool_name              # unique tool identifier
string description            # human-readable description
string action_server          # full action server path (e.g. /skills/speak)
string parameters_json        # JSON array of {name, type, required, description}
string category               # e.g. "communication", "navigation"
string[] tags                 # e.g. ["tts", "speech"]
---
bool success
string message
```

### `DeregisterTool.srv`
```
string tool_name
---
bool success
string message
```

### `ListTools.srv`
```
---
string[] tool_names
string[] descriptions
string[] action_servers
string[] parameters_json      # one JSON array per tool
string[] categories
```

Update **`andr/CMakeLists.txt`** to include these three new `.srv` files in `rosidl_generate_interfaces`.

---

## Step 2: Refactor `skill_executor/` → C++ Tool Manager

Evolve the existing `skill_executor` package into `tool_manager`:

- **Rename** the package directory from `skill_executor/` to `tool_manager/`
- **Keep it C++** (ament_cmake) — compiled, lean
- Replace YAML config loading with **three ROS2 service servers**:
  - `/tool_manager/register` (RegisterTool)
  - `/tool_manager/deregister` (DeregisterTool)
  - `/tool_manager/list` (ListTools)
- **Keep the ExecuteSkill action server** at `/tool_manager/execute` — same routing logic, but the skill_map is populated dynamically via RegisterTool calls instead of YAML
- On register: create an action client for the tool's action_server, store in the map
- On deregister: remove from map, destroy action client
- On list: return all currently registered tools and their metadata

### Files to modify:
- `tool_manager/include/tool_manager/tool_manager.h` — new header (rename from skill_executor.h)
- `tool_manager/src/tool_manager.cpp` — new impl (evolve from skill_executor.cpp)
- `tool_manager/CMakeLists.txt` — update package name, deps
- `tool_manager/package.xml` — update package name, deps

### Key changes from current skill_executor:
- Remove `load_config()` and yaml-cpp dependency
- Add `ToolEntry` struct (replaces `SkillEntry`, adds description, parameters_json, category, tags)
- Add service server callbacks: `handle_register`, `handle_deregister`, `handle_list`
- Action server name changes from `skill_executor` to `tool_manager/execute`
- Thread-safe access to tool_map_ (std::mutex) since services and action server run concurrently

---

## Step 3: Create `andr_tools/` Python package with `BaseAgentTool`

New package: **`andr_tools/`** (ament_python)

### `andr_tools/andr_tools/base_agent_tool.py`

```python
class BaseAgentTool(Node):
    """
    Base class for all agent tools. Subclasses provide:
    - TOOL_NAME: str
    - TOOL_DESCRIPTION: str
    - TOOL_PARAMETERS: list[dict]  # [{name, type, required, description}]
    - TOOL_CATEGORY: str
    - TOOL_TAGS: list[str]
    - _execute(params: dict, goal_handle) -> ExecuteSkill.Result

    Optional:
    - ParamsType: a dataclass/Pydantic model for typed params
    """
```

**Responsibilities:**
- Creates a ROS2 Node named `{tool_name}_server`
- Creates an `ActionServer(ExecuteSkill)` at `/tools/{tool_name}`
- On init: calls `/tool_manager/register` service to register itself
- On destroy/shutdown: calls `/tool_manager/deregister` to deregister
- Handles goal/cancel callbacks with sensible defaults
- In execute callback:
  1. Parse `params_json` from goal
  2. If subclass defines `ParamsType`, convert JSON dict to that type
  3. Call subclass `_execute(params, goal_handle)`
  4. Return the result

### Custom typing support:
```python
class SpeakTool(BaseAgentTool):
    TOOL_NAME = "speak"
    # ...

    @dataclass
    class ParamsType:
        text: str
        voice: str = "default"

    def _execute(self, params: ParamsType, goal_handle):
        # params is already typed! params.text, params.voice
        ...
```

The base class handles JSON → ParamsType conversion. If no ParamsType is defined, params is a plain dict.

### Package structure:
```
andr_tools/
├── package.xml
├── setup.py
├── andr_tools/
│   ├── __init__.py
│   └── base_agent_tool.py
```

---

## Step 4: Migrate existing skill servers to `BaseAgentTool`

Refactor `robot_skills/` servers to extend `BaseAgentTool`:

### Before (speak_server.py — ~78 lines):
```python
class SpeakServer(Node):
    def __init__(self):
        super().__init__("speak_server")
        self._action_server = ActionServer(...)
    def _goal_cb(self, ...): ...
    def _cancel_cb(self, ...): ...
    def _execute_cb(self, goal_handle): ...
```

### After (~30 lines):
```python
from andr_tools import BaseAgentTool

class SpeakTool(BaseAgentTool):
    TOOL_NAME = "speak"
    TOOL_DESCRIPTION = "Synthesise and play text via robot speaker"
    TOOL_PARAMETERS = [
        {"name": "text", "type": "string", "required": True, "description": "Sentence to speak"},
        {"name": "voice", "type": "string", "required": False, "description": "TTS voice style"},
    ]
    TOOL_CATEGORY = "communication"
    TOOL_TAGS = ["tts", "speech"]

    def _execute(self, params, goal_handle):
        text = params.get("text", "")
        # ... mock TTS logic ...
        return {"status": "done", "text_spoken": text}
```

Migrate: `speak_server.py`, `spin_server.py`, `walk_server.py`. Leave `navigate_to_point_server.py` and `go_to_point_server.py` for now (they have complex service client dependencies).

---

## Step 5: Update agent-side to use Tool Manager

### `agent/agent/skills.py`
- `SkillExecutor` changes its action client target from `/skill_executor` to `/tool_manager/execute`
- `SkillsRegistry` gets a new method `from_tool_manager(node)` that calls `/tool_manager/list` service to discover available tools dynamically
- Keep `from_yaml()` as a fallback but make `from_tool_manager()` the primary path
- The agent now knows what tools are online without needing skills.yaml

### `agent/agent/tools.py`
- No structural changes needed — it already builds LangChain tools from the registry
- Just needs the registry to be populated from tool_manager instead of YAML

### `agent/agent/agent.py`
- On startup, call `SkillsRegistry.from_tool_manager()` instead of `from_yaml()`
- Optionally: refresh the registry periodically or on-demand

---

## Step 6: Update launch file

### `andr/launch/andr.launch.py`
- Replace `skill_executor_node` with `tool_manager_node` (new package name, new executable)
- Remove the `config_yaml` parameter (no longer needed)
- Update the `launch_skills` argument description
- Skill server nodes remain as-is (they self-register on startup)

---

## Step 7: Cleanup

- Remove `andr/config/skill_executor_config.yaml` (no longer needed — tools self-register)
- Keep `andr/config/skills.yaml` as documentation/fallback but it's no longer the primary source
- Update `robot_skills/setup.py` to add `andr_tools` as a dependency
- Update `robot_skills/package.xml` to depend on `andr_tools`

---

## Execution Order

1. **Step 1** — New .srv interfaces (must build first, everything depends on these)
2. **Step 2** — C++ Tool Manager (replaces skill_executor, provides services)
3. **Step 3** — Python BaseAgentTool package (depends on interfaces + tool_manager services)
4. **Step 4** — Migrate skill servers (depends on BaseAgentTool)
5. **Step 5** — Update agent-side (depends on tool_manager running)
6. **Step 6** — Update launch file
7. **Step 7** — Cleanup
