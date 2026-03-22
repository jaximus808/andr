"""vision_task_bridge.py — Bridges VLM scene observations into the task pipeline.

Subscribes to /vision/scene (published by VisionNode) and forwards
noteworthy observations as tasks through /task_manager/execute — the
same path the UI uses. The agent never knows the prompt came from
a camera; it just receives a task and decides what tools to call.

This keeps the perception layer fully decoupled from the agent layer.

ROS 2 parameters
----------------
scene_topic       string   /vision/scene           Topic with VLM scene descriptions.
cooldown_sec      float    10.0                    Min seconds between forwarded tasks.
"""

from __future__ import annotations

import logging
import threading
import time

import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String

from andr.action import TaskGoal

logger = logging.getLogger(__name__)


class VisionTaskBridge(Node):
    """Watches VLM scene descriptions and sends actionable ones as tasks."""

    def __init__(self):
        super().__init__("vision_task_bridge")

        self.declare_parameter("scene_topic", "/vision/scene")
        self.declare_parameter("cooldown_sec", 10.0)

        scene_topic = self._str("scene_topic")
        self._cooldown = self.get_parameter(
            "cooldown_sec"
        ).get_parameter_value().double_value
        self._last_task_time = 0.0
        self._task_lock = threading.Lock()

        self._cb_group = ReentrantCallbackGroup()

        # Action client to the task manager — same interface the UI uses
        self._task_client = ActionClient(
            self, TaskGoal, "/task_manager/execute",
            callback_group=self._cb_group,
        )

        # Subscribe to scene descriptions from VisionNode
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._scene_sub = self.create_subscription(
            String, scene_topic, self._scene_cb, qos,
        )

        self.get_logger().info(
            f"VisionTaskBridge ready — {scene_topic} → /task_manager/execute "
            f"(cooldown={self._cooldown}s)"
        )

    def _str(self, name: str) -> str:
        return self.get_parameter(name).get_parameter_value().string_value

    def _scene_cb(self, msg: String) -> None:
        """Evaluate the scene description and forward if actionable."""
        description = msg.data.strip()
        if not description:
            return

        # Cooldown — don't flood the agent with tasks
        now = time.monotonic()
        with self._task_lock:
            if now - self._last_task_time < self._cooldown:
                return
            self._last_task_time = now

        # Build a task prompt from the scene observation
        prompt = (
            f"You are observing the following through your camera:\n\n"
            f"\"{description}\"\n\n"
            f"Respond appropriately to what you see. If someone is interacting "
            f"with you (waving, speaking, gesturing), respond socially. "
            f"If nothing requires your attention, simply acknowledge what you see."
        )

        self._send_task(prompt, context=f"vision_observation: {description}")

    def _send_task(self, prompt: str, context: str = "") -> None:
        """Send a task through the task_manager pipeline."""
        if not self._task_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().warn("task_manager not available — dropping vision task")
            return

        goal = TaskGoal.Goal()
        goal.prompt = prompt
        goal.context = context

        self.get_logger().info(f"Sending vision task: {prompt[:100]}...")

        future = self._task_client.send_goal_async(goal)
        future.add_done_callback(self._on_goal_response)

    def _on_goal_response(self, future) -> None:
        try:
            goal_handle = future.result()
            if goal_handle is None or not goal_handle.accepted:
                self.get_logger().warn("Vision task was rejected by task_manager")
                return
            self.get_logger().info("Vision task accepted by task_manager")
            # Get result asynchronously — don't block
            result_future = goal_handle.get_result_async()
            result_future.add_done_callback(self._on_task_result)
        except Exception as exc:
            self.get_logger().error(f"Failed to send vision task: {exc}")

    def _on_task_result(self, future) -> None:
        try:
            wrapped = future.result()
            if wrapped is None:
                return
            result = wrapped.result
            self.get_logger().info(
                f"Vision task {'succeeded' if result.success else 'failed'}: "
                f"{result.summary[:120]}"
            )
        except Exception as exc:
            self.get_logger().error(f"Vision task result error: {exc}")


def main(args=None):
    logging.basicConfig(level=logging.DEBUG)
    rclpy.init(args=args)
    node = VisionTaskBridge()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
