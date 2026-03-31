"""memory_tools.py — BaseAgentTool wrappers for the memory_manager.

These tools register with tool_manager so the agent can discover and use
them like any other tool.  Internally they call the memory_manager ROS 2
services.

Tools
-----
store_memory   — Store information in long-term memory
query_memory   — Search long-term memory for relevant knowledge

Usage
-----
# Launch both tools in one process:
ros2 run agent memory_tools

# Or individually:
ros2 run agent store_memory_server
ros2 run agent query_memory_server
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor

from andr_msgs.action import ExecuteSkill
from andr_msgs.srv import StoreMemory, QueryMemory
from andr_tools import BaseAgentTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# store_memory tool
# ---------------------------------------------------------------------------

class StoreMemoryTool(BaseAgentTool):
    """Agent tool that stores information in the robot's long-term memory."""

    TOOL_NAME = "store_memory"
    TOOL_DESCRIPTION = (
        "Store information in the robot's long-term memory for future recall. "
        "Use this to remember facts, observations, user preferences, locations, "
        "or anything the robot should know later."
    )
    TOOL_PARAMETERS = [
        {
            "name": "text",
            "type": "string",
            "required": True,
            "description": "The information to remember",
        },
        {
            "name": "metadata_json",
            "type": "string",
            "required": False,
            "description": (
                "Optional JSON dict of metadata tags "
                '(e.g. {"source": "user", "topic": "preferences"})'
            ),
        },
        {
            "name": "store_name",
            "type": "string",
            "required": False,
            "description": "Target memory store name (empty = default store)",
        },
    ]
    TOOL_CATEGORY = "memory"
    TOOL_TAGS = ["memory", "rag", "store", "remember"]

    @dataclass
    class ParamsType:
        text: str
        metadata_json: str = ""
        store_name: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mem_cb_group = ReentrantCallbackGroup()
        self._store_client = self.create_client(
            StoreMemory, "memory_manager/store",
            callback_group=self._mem_cb_group,
        )

    def _execute(self, params: ParamsType, goal_handle) -> dict:
        # Wait for memory_manager service
        if not self._store_client.wait_for_service(timeout_sec=5.0):
            return {
                "status": "error",
                "message": "memory_manager/store service not available",
            }

        # Build request
        req = StoreMemory.Request()
        req.text = params.text
        req.metadata_json = params.metadata_json
        req.store_name = params.store_name

        # Publish progress feedback
        feedback = ExecuteSkill.Feedback()
        feedback.status = "storing_memory"
        feedback.progress = 0.5
        goal_handle.publish_feedback(feedback)

        # Call service synchronously
        future = self._store_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=10.0)

        result = future.result()
        if result is None:
            return {"status": "error", "message": "Service call timed out"}

        if result.success:
            self.get_logger().info(f"Stored memory: {params.text[:80]}...")
            return {
                "status": "done",
                "id": result.id,
                "message": result.message,
            }
        else:
            return {"status": "error", "message": result.message}


# ---------------------------------------------------------------------------
# query_memory tool
# ---------------------------------------------------------------------------

class QueryMemoryTool(BaseAgentTool):
    """Agent tool that searches the robot's long-term memory."""

    TOOL_NAME = "query_memory"
    TOOL_DESCRIPTION = (
        "Search the robot's long-term memory for relevant past knowledge, "
        "observations, or experiences. Use this when you need background "
        "information, past context, user preferences, or spatial knowledge "
        "before acting."
    )
    TOOL_PARAMETERS = [
        {
            "name": "query",
            "type": "string",
            "required": True,
            "description": "Natural language search query",
        },
        {
            "name": "top_k",
            "type": "int",
            "required": False,
            "description": "Max results to return (default: 4)",
        },
        {
            "name": "store_name",
            "type": "string",
            "required": False,
            "description": (
                "Specific memory store to search (empty = search all stores)"
            ),
        },
    ]
    TOOL_CATEGORY = "memory"
    TOOL_TAGS = ["memory", "rag", "query", "recall", "knowledge"]

    @dataclass
    class ParamsType:
        query: str
        top_k: int = 0
        store_name: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mem_cb_group = ReentrantCallbackGroup()
        self._query_client = self.create_client(
            QueryMemory, "memory_manager/query",
            callback_group=self._mem_cb_group,
        )

    def _execute(self, params: ParamsType, goal_handle) -> dict:
        # Wait for memory_manager service
        if not self._query_client.wait_for_service(timeout_sec=5.0):
            return {
                "status": "error",
                "message": "memory_manager/query service not available",
            }

        # Build request
        req = QueryMemory.Request()
        req.query = params.query
        req.top_k = params.top_k
        req.store_name = params.store_name

        # Publish progress feedback
        feedback = ExecuteSkill.Feedback()
        feedback.status = "querying_memory"
        feedback.progress = 0.5
        goal_handle.publish_feedback(feedback)

        # Call service synchronously
        future = self._query_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=10.0)

        result = future.result()
        if result is None:
            return {"status": "error", "message": "Service call timed out"}

        if result.success:
            results = json.loads(result.results_json)
            # Format results for the agent
            if not results:
                return {
                    "status": "done",
                    "results": [],
                    "message": f"No relevant memories found for: '{params.query}'",
                    "formatted": f"No relevant memories found for: '{params.query}'",
                }

            # Build formatted text block for the agent
            lines = [f"Found {len(results)} relevant memory(s):"]
            for i, r in enumerate(results, 1):
                score = r.get("score", 0)
                content = r.get("content", "")
                meta = r.get("metadata", {})
                source = meta.get("source", "memory")
                store = meta.get("_store", "")
                store_tag = f" [{store}]" if store else ""
                lines.append(
                    f"  {i}. (score={score:.2f}{store_tag} src={source}) {content}"
                )

            return {
                "status": "done",
                "results": results,
                "message": result.message,
                "formatted": "\n".join(lines),
            }
        else:
            return {"status": "error", "message": result.message}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def store_main(args=None):
    """Launch only the store_memory tool."""
    rclpy.init(args=args)
    node = StoreMemoryTool()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


def query_main(args=None):
    """Launch only the query_memory tool."""
    rclpy.init(args=args)
    node = QueryMemoryTool()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main(args=None):
    """Launch both memory tools in a single process."""
    rclpy.init(args=args)
    store_node = StoreMemoryTool()
    query_node = QueryMemoryTool()
    executor = MultiThreadedExecutor()
    executor.add_node(store_node)
    executor.add_node(query_node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        store_node.destroy_node()
        query_node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
