"""base_input_source.py — Base class for standardized task input sources.

Subclass this to create an input source that sends tasks to the agent
through the task_manager pipeline. This is the input-side counterpart
to BaseAgentTool (which standardizes the output/tool side).

Every input source — UI, vision, scheduled tasks, SMS, voice, etc. —
follows the same pattern: observe something, build a prompt, send it
through task_manager. This base class handles the plumbing.

Example
-------
::

    class VisionTaskBridge(BaseInputSource):
        SOURCE_NAME = "vision"
        SOURCE_DESCRIPTION = "Forwards VLM scene observations as agent tasks"

        def __init__(self):
            super().__init__()
            self.create_subscription(String, "/vision/scene", self._on_scene, 10)

        def _on_scene(self, msg):
            self.send_task(
                prompt=f"You see: {msg.data}. Respond appropriately.",
                context=f"vision_observation: {msg.data}",
            )
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, ClassVar

import rclpy
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from andr_msgs.action import TaskGoal

logger = logging.getLogger(__name__)


class BaseInputSource(Node):
    """Base ROS 2 node for input sources that feed tasks into the agent.

    Subclasses **must** define:
        SOURCE_NAME        — unique identifier for this input source (snake_case)
        SOURCE_DESCRIPTION — human-readable description of what this source does

    Subclasses **should** override:
        on_task_accepted(prompt)   — called when task_manager accepts the task
        on_task_rejected(prompt)   — called when task_manager rejects the task
        on_task_completed(prompt, success, summary) — called when agent finishes
        on_task_feedback(state, status, progress)   — called on agent progress

    The base class provides:
        send_task(prompt, context)  — send a task through the standard pipeline
        is_busy                     — True if a task from this source is in-flight
    """

    # ── Class-level config (override in subclass) ─────────────────────────
    SOURCE_NAME: ClassVar[str] = ""
    SOURCE_DESCRIPTION: ClassVar[str] = ""

    def __init__(self, **kwargs: Any):
        if not self.SOURCE_NAME:
            raise ValueError("SOURCE_NAME must be set in subclass")

        node_name = f"{self.SOURCE_NAME}_input"
        super().__init__(node_name, **kwargs)

        self._cb_group = ReentrantCallbackGroup()
        self._busy = False

        # ── Action client to task_manager ─────────────────────────────────
        self._task_client = ActionClient(
            self, TaskGoal, "/task_manager/execute",
            callback_group=self._cb_group,
        )

        self.get_logger().info(
            f"InputSource '{self.SOURCE_NAME}' ready — "
            f"{self.SOURCE_DESCRIPTION}"
        )

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_busy(self) -> bool:
        """True if a task sent by this source is currently being executed."""
        return self._busy

    def send_task(self, prompt: str, context: str = "") -> bool:
        """Send a task through the task_manager pipeline.

        Returns True if the task was dispatched, False if task_manager
        is unavailable.
        """
        if not prompt.strip():
            self.get_logger().warn(f"[{self.SOURCE_NAME}] Ignoring empty prompt")
            return False

        if not self._task_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().warn(
                f"[{self.SOURCE_NAME}] task_manager not available — dropping task"
            )
            return False

        goal = TaskGoal.Goal()
        goal.prompt = prompt
        goal.context = context

        self._busy = True
        self.get_logger().info(
            f"[{self.SOURCE_NAME}] Sending task: {prompt[:100]}..."
        )

        future = self._task_client.send_goal_async(
            goal,
            feedback_callback=self._on_feedback_wrapper,
        )
        future.add_done_callback(self._on_goal_response_wrapper)
        return True

    # ── Hooks for subclasses to override ──────────────────────────────────

    def on_task_accepted(self, prompt: str) -> None:
        """Called when the task_manager accepts the task."""
        pass

    def on_task_rejected(self, prompt: str) -> None:
        """Called when the task_manager rejects the task."""
        pass

    def on_task_completed(self, prompt: str, success: bool, summary: str) -> None:
        """Called when the agent finishes processing the task."""
        pass

    def on_task_feedback(self, state: str, status: str, progress: float) -> None:
        """Called on each progress update from the agent."""
        pass

    # ── Internal callbacks ────────────────────────────────────────────────

    def _on_goal_response_wrapper(self, future) -> None:
        try:
            goal_handle = future.result()
            if goal_handle is None or not goal_handle.accepted:
                self._busy = False
                prompt = "(unknown)"
                self.get_logger().warn(
                    f"[{self.SOURCE_NAME}] Task rejected by task_manager"
                )
                self.on_task_rejected(prompt)
                return

            self.get_logger().info(
                f"[{self.SOURCE_NAME}] Task accepted by task_manager"
            )
            self.on_task_accepted("")

            result_future = goal_handle.get_result_async()
            result_future.add_done_callback(self._on_result_wrapper)
        except Exception as exc:
            self._busy = False
            self.get_logger().error(
                f"[{self.SOURCE_NAME}] Goal response error: {exc}"
            )

    def _on_feedback_wrapper(self, feedback_msg) -> None:
        try:
            fb = feedback_msg.feedback
            self.on_task_feedback(fb.state, fb.status, fb.progress)
        except Exception as exc:
            self.get_logger().error(
                f"[{self.SOURCE_NAME}] Feedback handler error: {exc}"
            )

    def _on_result_wrapper(self, future) -> None:
        self._busy = False
        try:
            wrapped = future.result()
            if wrapped is None:
                self.get_logger().warn(
                    f"[{self.SOURCE_NAME}] Task returned no result"
                )
                self.on_task_completed("", False, "No result returned")
                return

            result = wrapped.result
            self.get_logger().info(
                f"[{self.SOURCE_NAME}] Task "
                f"{'succeeded' if result.success else 'failed'}: "
                f"{result.summary[:120]}"
            )
            self.on_task_completed("", result.success, result.summary)
        except Exception as exc:
            self.get_logger().error(
                f"[{self.SOURCE_NAME}] Result handler error: {exc}"
            )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def destroy_node(self) -> None:
        self.get_logger().info(
            f"Shutting down input source '{self.SOURCE_NAME}'"
        )
        super().destroy_node()
