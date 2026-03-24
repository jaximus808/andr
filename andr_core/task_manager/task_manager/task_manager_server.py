"""
task_manager_server.py — ROS 2 action server that bridges UI tasks to the agent.

Flow:
  1. UI sends a TaskGoal to /task_manager/execute
  2. This node forwards the prompt to the agent's /agent/prompt action
  3. Agent feedback is relayed back as TaskGoal feedback
  4. Agent result is returned as TaskGoal result
"""

from __future__ import annotations

import logging
import threading

import rclpy
from rclpy.action import ActionServer, ActionClient, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from andr_msgs.action import TaskGoal, Agent

logger = logging.getLogger(__name__)


class TaskManagerServer(Node):
    """Receives tasks from the UI and delegates to the agent action server."""

    TASK_ACTION = "/task_manager/execute"
    AGENT_ACTION = "agent/prompt"

    def __init__(self):
        super().__init__("task_manager")

        self._cb_group = ReentrantCallbackGroup()
        self._current_agent_goal_handle = None
        self._cancel_requested = threading.Event()

        # Action client to talk to the agent
        self._agent_client = ActionClient(
            self, Agent, self.AGENT_ACTION,
            callback_group=self._cb_group,
        )

        # Action server for the UI to send tasks to
        self._action_server = ActionServer(
            self,
            TaskGoal,
            self.TASK_ACTION,
            execute_callback=self._execute_cb,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
            callback_group=self._cb_group,
        )

        self.get_logger().info(
            f"TaskManager ready — serving '{self.TASK_ACTION}', "
            f"forwarding to '{self.AGENT_ACTION}'"
        )

    # ------------------------------------------------------------------
    # Goal / Cancel callbacks
    # ------------------------------------------------------------------

    def _goal_cb(self, goal_request) -> GoalResponse:
        prompt = goal_request.prompt.strip()
        if not prompt:
            self.get_logger().warn("Rejecting task: empty prompt.")
            return GoalResponse.REJECT
        self.get_logger().info(f"Accepting task — prompt: '{prompt[:80]}'")
        return GoalResponse.ACCEPT

    def _cancel_cb(self, goal_handle) -> CancelResponse:
        self.get_logger().info("Cancel requested for task — forwarding to agent.")
        self._cancel_requested.set()
        # Forward cancellation to the agent
        if self._current_agent_goal_handle is not None:
            self._current_agent_goal_handle.cancel_goal_async()
        return CancelResponse.ACCEPT

    # ------------------------------------------------------------------
    # Execute — forward to agent and relay feedback
    # ------------------------------------------------------------------

    def _execute_cb(self, goal_handle) -> TaskGoal.Result:
        goal = goal_handle.request
        result = TaskGoal.Result()
        self._cancel_requested.clear()

        self.get_logger().info(f"Task received: '{goal.prompt[:80]}'")

        # Wait for agent action server to be available
        if not self._agent_client.wait_for_server(timeout_sec=10.0):
            msg = "Agent action server not available after 10s."
            self.get_logger().error(msg)
            goal_handle.abort()
            result.success = False
            result.summary = msg
            return result

        # Send goal to agent
        agent_goal = Agent.Goal()
        agent_goal.prompt = goal.prompt
        agent_goal.context = goal.context

        self._send_task_feedback(goal_handle, "thinking", "Sending task to agent…", 0.05)

        send_future = self._agent_client.send_goal_async(
            agent_goal,
            feedback_callback=lambda fb: self._on_agent_feedback(goal_handle, fb),
        )
        send_event = threading.Event()
        send_future.add_done_callback(lambda _: send_event.set())
        if not send_event.wait(timeout=10.0):
            msg = "Agent goal send timed out after 10s."
            self.get_logger().error(msg)
            goal_handle.abort()
            result.success = False
            result.summary = msg
            return result

        agent_goal_handle = send_future.result()
        if agent_goal_handle is None or not agent_goal_handle.accepted:
            msg = "Agent rejected the task goal."
            self.get_logger().error(msg)
            goal_handle.abort()
            result.success = False
            result.summary = msg
            return result

        # Track the running agent goal so cancel can forward to it
        self._current_agent_goal_handle = agent_goal_handle

        self.get_logger().info("Agent accepted goal — waiting for result…")
        self._send_task_feedback(goal_handle, "executing", "Agent is working…", 0.1)

        # Wait for agent to finish or cancellation
        result_future = agent_goal_handle.get_result_async()
        result_event = threading.Event()
        result_future.add_done_callback(lambda _: result_event.set())

        # Poll so we can detect cancellation
        while not result_event.wait(timeout=0.5):
            if self._cancel_requested.is_set():
                self.get_logger().info("Task cancelled — aborting agent goal.")
                agent_goal_handle.cancel_goal_async()
                # Give agent a moment to wrap up
                result_event.wait(timeout=5.0)
                break

        self._current_agent_goal_handle = None

        # Check if this was a cancellation
        if self._cancel_requested.is_set():
            self._cancel_requested.clear()
            self._send_task_feedback(goal_handle, "cancelled", "Task was cancelled", 1.0)
            goal_handle.canceled()
            result.success = False
            result.summary = "Task cancelled"
            return result

        wrapped = result_future.result()
        if wrapped is None:
            msg = "Agent returned no result."
            self.get_logger().error(msg)
            goal_handle.abort()
            result.success = False
            result.summary = msg
            return result

        agent_result = wrapped.result

        # Relay agent result back to the UI
        result.success = agent_result.success
        result.summary = agent_result.summary

        if agent_result.success:
            self._send_task_feedback(goal_handle, "done", agent_result.summary, 1.0)
            goal_handle.succeed()
        else:
            self._send_task_feedback(goal_handle, "failed", agent_result.summary, 1.0)
            goal_handle.abort()

        self.get_logger().info(
            f"Task {'succeeded' if result.success else 'failed'}: {result.summary[:120]}"
        )
        return result

    # ------------------------------------------------------------------
    # Feedback relay
    # ------------------------------------------------------------------

    def _on_agent_feedback(self, task_goal_handle, feedback_msg) -> None:
        """Relay agent feedback to the task caller."""
        agent_fb = feedback_msg.feedback
        self._send_task_feedback(
            task_goal_handle,
            state=agent_fb.state,
            status=agent_fb.status,
            progress=agent_fb.progress,
        )

    def _send_task_feedback(self, goal_handle, state, status, progress) -> None:
        fb = TaskGoal.Feedback()
        fb.state = state
        fb.status = status
        fb.progress = float(max(0.0, min(1.0, progress)))
        goal_handle.publish_feedback(fb)

    # ------------------------------------------------------------------

    def destroy(self):
        self._action_server.destroy()
        self._agent_client.destroy()
        super().destroy_node()


def main(args=None):
    logging.basicConfig(level=logging.DEBUG)
    rclpy.init(args=args)
    server = TaskManagerServer()
    executor = MultiThreadedExecutor()
    executor.add_node(server)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        server.destroy()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
