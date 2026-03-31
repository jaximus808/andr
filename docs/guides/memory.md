# Memory

ANDR's memory system gives the agent persistent, long-term knowledge that survives across sessions. It uses RAG (Retrieval-Augmented Generation) backed by ChromaDB to store and retrieve information.

## How it works

```
                          ROS 2 services
  store_memory tool ──────► memory_manager ──────► ChromaDB (disk)
  query_memory tool ──────►      │
                                 │
                                 ├── store: "default"  (~/. andr/memory/default)
                                 ├── store: "long_term" (/mnt/external/memory)
                                 └── store: ...
```

The **memory_manager** is a standalone ROS 2 node that owns all configured memory stores. It exposes four services:

| Service | Type | Description |
|---|---|---|
| `memory_manager/store` | `StoreMemory` | Add an entry to a store |
| `memory_manager/query` | `QueryMemory` | Search for relevant entries |
| `memory_manager/list_stores` | `ListMemoryStores` | List all configured stores |
| `memory_manager/status` | `GetMemoryStatus` | Detailed status of one store |

The agent interacts with memory through two **BaseAgentTool** action servers:

- **`store_memory`** — The agent calls this to remember facts, observations, or user preferences
- **`query_memory`** — The agent calls this to recall relevant past knowledge before acting

Both tools register with `tool_manager` automatically and are discovered by the agent at runtime.

## Configuration

### pip projects (`andr.config.yaml`)

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
      on_full: warn
```

### colcon workspace (`stack.yaml`)

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
      on_full: warn

core:
  memory_manager:
    enabled: true

tools:
  store_memory:
    enabled: true
    package: agent
    executable: store_memory_server
  query_memory:
    enabled: true
    package: agent
    executable: query_memory_server
```

### CLI flags

```bash
andr start --memory-path ~/.andr/memory/default \
           --memory-max-size-mb 512 \
           --memory-on-full warn
```

## Multi-store setup

You can configure multiple memory stores on different disks. This is useful for separating short-term working memory from large archival storage, or for spreading load across disks.

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
      on_full: warn

    long_term:
      backend: chroma
      path: /mnt/external/andr_memory
      max_size_mb: 2048
      embedding_model: all-MiniLM-L6-v2
      on_full: evict

    user_prefs:
      backend: chroma
      path: /home/user/.andr/memory/prefs
      max_size_mb: 128
      embedding_model: all-MiniLM-L6-v2
      on_full: reject
```

When the agent calls `query_memory` without specifying a store, the memory manager **fans out across all stores**, merges results by relevance score, and returns the top_k best matches.

To target a specific store, pass the `store_name` parameter:

```
store_memory(text="User prefers metric units", store_name="user_prefs")
query_memory(query="unit preferences", store_name="user_prefs")
```

## Size management

Each store has a `max_size_mb` limit (0 = unlimited) and an `on_full` policy:

| Policy | Behavior |
|---|---|
| `reject` | Refuse new entries when the store hits its size limit |
| `evict` | Delete oldest entries to make room (FIFO eviction) |
| `warn` | Log a warning but allow storage anyway |

## Using memory from custom tools

Any tool can store or query memories by calling the memory_manager services:

```python
from andr_msgs.srv import StoreMemory, QueryMemory

# In your BaseAgentTool subclass:
class MyTool(BaseAgentTool):
    def __init__(self):
        super().__init__()
        self._mem_client = self.create_client(
            StoreMemory, "memory_manager/store"
        )

    def _execute(self, params, goal_handle):
        # Store something in memory
        req = StoreMemory.Request()
        req.text = "Important observation from my tool"
        req.metadata_json = '{"source": "my_tool"}'
        req.store_name = ""  # empty = default store
        future = self._mem_client.call_async(req)
        rclpy.spin_until_future_complete(self, future)
        return {"status": "done"}
```

## Interacting with memory via ROS 2

```bash
# Store a memory
ros2 service call /memory_manager/store andr_msgs/srv/StoreMemory \
  "{text: 'The charger is in room 3', metadata_json: '{\"source\": \"user\"}', store_name: ''}"

# Query memories
ros2 service call /memory_manager/query andr_msgs/srv/QueryMemory \
  "{query: 'where is the charger', top_k: 4, store_name: ''}"

# List all stores
ros2 service call /memory_manager/list_stores andr_msgs/srv/ListMemoryStores

# Get status of a store
ros2 service call /memory_manager/status andr_msgs/srv/GetMemoryStatus \
  "{store_name: 'default'}"
```

## Architecture

The memory system follows ANDR's core principles:

- **Agent is tool-agnostic**: The agent doesn't know about ChromaDB or memory internals. It discovers `store_memory` and `query_memory` through tool_manager like any other tool.
- **Everything is a tool**: Memory access is through registered BaseAgentTools.
- **Modular**: The memory_manager is a standalone node. Swap the backend, change paths, add stores — all without touching agent code.
