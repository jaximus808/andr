"""vision_task_bridge.py — Bridges VLM scene observations into the task pipeline.

Subscribes to /vision/scene (published by VisionNode) and forwards
noteworthy observations as tasks through /task_manager/execute — the
same path the UI uses. The agent never knows the prompt came from
a camera; it just receives a task and decides what tools to call.

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
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String

from andr_tools import BaseInputSource

logger = logging.getLogger(__name__)


class VisionTaskBridge(BaseInputSource):
    """Watches VLM scene descriptions and sends actionable ones as tasks."""

    SOURCE_NAME = "vision"
    SOURCE_DESCRIPTION = "Forwards VLM scene observations as agent tasks"

    def __init__(self):
        super().__init__()

        self.declare_parameter("scene_topic", "/vision/scene")
        self.declare_parameter("cooldown_sec", 10.0)

        scene_topic = self.get_parameter(
            "scene_topic"
        ).get_parameter_value().string_value
        self._cooldown = self.get_parameter(
            "cooldown_sec"
        ).get_parameter_value().double_value
        self._last_task_time = 0.0
        self._task_lock = threading.Lock()

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(String, scene_topic, self._on_scene, qos)

        self.get_logger().info(
            f"Watching {scene_topic} (cooldown={self._cooldown}s)"
        )

    def _on_scene(self, msg: String) -> None:
        description = msg.data.strip()
        if not description:
            return

        # Cooldown — don't flood the agent with tasks
        now = time.monotonic()
        with self._task_lock:
            if now - self._last_task_time < self._cooldown:
                return
            self._last_task_time = now

        prompt = (
            f"You are observing the following through your camera:\n\n"
            f"\"{description}\"\n\n"
            f"Respond appropriately to what you see. If someone is interacting "
            f"with you (waving, speaking, gesturing), respond socially. "
            f"If nothing requires your attention, simply acknowledge what you see."
        )

        self.send_task(prompt, context=f"vision_observation: {description}")


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
