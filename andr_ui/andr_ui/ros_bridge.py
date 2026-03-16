"""
ros_bridge.py — ROS 2 node that:
  • Subscribes to robot status topics and pushes events to connected WebSocket clients.
  • Sends task goals to /task_manager/execute when the UI sends a prompt.
  • Periodically discovers active nodes and action servers for the status panel.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Callable

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String

from andr.msg import Prompt, RobotSpeech
from andr.action import TaskGoal


class RosBridgeNode(Node):
    """Lightweight ROS node that shuttles data between ROS topics and asyncio queues."""

    def __init__(self, push_event: Callable[[dict], None]):
        super().__init__("andr_ui_bridge")

        self._push = push_event

        # ── Subscribers ──────────────────────────────────────────────────
        self.create_subscription(
            RobotSpeech, "/robot/speech", self._on_robot_speech, 10,
        )
        self.create_subscription(
            String, "/agent/feedback", self._on_agent_feedback, 10,
        )
        self.create_subscription(
            String, "/robot/status", self._on_robot_status, 10,
        )

        # ── Action client to task_manager ────────────────────────────────
        self._task_client = ActionClient(self, TaskGoal, "/task_manager/execute")

        # ── Publisher (kept for backwards compat / logging) ──────────────
        self._prompt_pub = self.create_publisher(Prompt, "/ui/prompt", 10)

        # ── Periodic node/action discovery (every 5s) ────────────────────
        self._discovery_timer = self.create_timer(5.0, self._discover_nodes)

        self.get_logger().info("RosBridgeNode ready")

    # ── Incoming topic handlers ──────────────────────────────────────────

    def _on_robot_speech(self, msg: RobotSpeech) -> None:
        self._push({
            "type": "robot_speech",
            "text": msg.text,
            "emotion": msg.emotion,
        })

    def _on_agent_feedback(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            data = {"raw": msg.data}
        self._push({"type": "agent_feedback", **data})

    def _on_robot_status(self, msg: String) -> None:
        self._push({"type": "robot_status", "text": msg.data})

    # ── Task submission (action client) ──────────────────────────────────

    def send_task(self, prompt: str, context: str = "") -> None:
        """Send a task to the task_manager action server. Non-blocking."""
        # Also publish on topic for logging
        pub_msg = Prompt()
        pub_msg.prompt = prompt
        pub_msg.context = context
        pub_msg.stamp = self.get_clock().now().to_msg()
        self._prompt_pub.publish(pub_msg)

        if not self._task_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().warn("task_manager not available — prompt published on topic only.")
            self._push({
                "type": "error",
                "text": "Task manager not available. Is task_manager_server running?",
            })
            return

        goal = TaskGoal.Goal()
        goal.prompt = prompt
        goal.context = context

        self.get_logger().info(f"Sending task to task_manager: '{prompt[:80]}'")
        self._task_client.send_goal_async(
            goal,
            feedback_callback=self._on_task_feedback,
        ).add_done_callback(self._on_task_goal_response)

    def _on_task_goal_response(self, future) -> None:
        goal_handle = future.result()
        if not goal_handle.accepted:
            self._push({"type": "error", "text": "Task was rejected by task_manager."})
            return

        self.get_logger().info("Task accepted by task_manager.")
        goal_handle.get_result_async().add_done_callback(self._on_task_result)

    def _on_task_feedback(self, feedback_msg) -> None:
        fb = feedback_msg.feedback
        self._push({
            "type": "task_feedback",
            "state": fb.state,
            "status": fb.status,
            "progress": fb.progress,
        })

    def _on_task_result(self, future) -> None:
        wrapped = future.result()
        if wrapped is None:
            self._push({"type": "error", "text": "Task timed out."})
            return
        res = wrapped.result
        self._push({
            "type": "task_result",
            "success": res.success,
            "summary": res.summary,
        })
        # Also show as a robot speech bubble
        self._push({
            "type": "robot_speech",
            "text": res.summary,
            "emotion": "satisfied" if res.success else "concerned",
        })

    # ── Node / action server discovery ───────────────────────────────────

    def _discover_nodes(self) -> None:
        """Push a snapshot of active nodes and action servers to the UI."""
        node_names = self.get_node_names_and_namespaces()
        nodes = [
            {"name": name, "namespace": ns}
            for name, ns in node_names
        ]

        # Discover action servers via topic conventions (*/_action/status)
        topic_list = self.get_topic_names_and_types()
        action_servers = set()
        for topic_name, _ in topic_list:
            if topic_name.endswith("/_action/status"):
                action_name = topic_name.rsplit("/_action/status", 1)[0]
                action_servers.add(action_name)

        self._push({
            "type": "node_status",
            "nodes": nodes,
            "action_servers": sorted(action_servers),
        })


# ── Spin ROS in a background thread ─────────────────────────────────────

def start_ros_thread(push_event: Callable[[dict], None]) -> RosBridgeNode:
    """Initialise rclpy and spin the bridge node in a daemon thread."""
    rclpy.init()
    node = RosBridgeNode(push_event)

    def _spin():
        try:
            rclpy.spin(node)
        finally:
            node.destroy_node()
            rclpy.shutdown()

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    return node
