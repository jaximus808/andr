"""go_to_point_server — skill that navigates to a named point on the current map.

Unlike navigate_to_point (which requires both point_name and map_name),
this skill only requires point_name. It queries map_manager/get_slam_config
to determine the currently loaded map, then resolves coordinates and
sends a Nav2 goal.

Flow:
  1. Parse params_json for point_name.
  2. Call map_manager/get_slam_config to get the current map name.
  3. Call map_manager/get_point_coordinates to resolve (x, y).
  4. Send a NavigateToPose goal to Nav2 (/navigate_to_pose).
  5. Forward Nav2 feedback (distance_remaining) as ExecuteSkill progress.
  6. Return success/failure result.
"""

import json
import threading

import rclpy
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose

from andr.action import ExecuteSkill
from andr.srv import GetPointCoordinates, GetSlamConfig


class GoToPointServer(Node):
    def __init__(self):
        super().__init__("go_to_point_server")

        cb_group = ReentrantCallbackGroup()

        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            "/skills/go_to_point",
            execute_callback=self._execute_cb,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
            callback_group=cb_group,
        )

        self._get_slam_config_client = self.create_client(
            GetSlamConfig,
            "map_manager/get_slam_config",
            callback_group=cb_group,
        )

        self._get_point_client = self.create_client(
            GetPointCoordinates,
            "map_manager/get_point_coordinates",
            callback_group=cb_group,
        )

        self._nav_client = ActionClient(
            self,
            NavigateToPose,
            "/navigate_to_pose",
            callback_group=cb_group,
        )

        self.get_logger().info(
            "GoToPointServer ready on '/skills/go_to_point'"
        )

    # ------------------------------------------------------------------
    # Goal / cancel callbacks
    # ------------------------------------------------------------------

    def _goal_cb(self, goal_request) -> GoalResponse:
        self.get_logger().info(
            f"go_to_point goal received: {goal_request.params_json}"
        )
        return GoalResponse.ACCEPT

    def _cancel_cb(self, goal_handle) -> CancelResponse:
        self.get_logger().info("go_to_point cancel requested")
        return CancelResponse.ACCEPT

    # ------------------------------------------------------------------
    # Execute callback
    # ------------------------------------------------------------------

    def _execute_cb(self, goal_handle) -> ExecuteSkill.Result:
        params = json.loads(goal_handle.request.params_json or "{}")
        point_name = params.get("point_name", "").strip()

        if not point_name:
            return self._fail(goal_handle, "'point_name' is required")

        # ── 1. Get the current map from SLAM config ────────────────────
        self._pub_feedback(goal_handle, "Getting current map...", 0.0)

        if not self._get_slam_config_client.wait_for_service(timeout_sec=5.0):
            return self._fail(
                goal_handle,
                "map_manager/get_slam_config service not available",
            )

        config_future = self._get_slam_config_client.call_async(GetSlamConfig.Request())
        config_event = threading.Event()
        config_future.add_done_callback(lambda _: config_event.set())
        config_event.wait(timeout=10.0)

        if not config_future.done() or config_future.result() is None:
            return self._fail(goal_handle, "Timed out getting SLAM config")

        config_resp = config_future.result()
        if not config_resp.success:
            return self._fail(goal_handle, f"SLAM config error: {config_resp.message}")

        map_name = config_resp.map_name
        if not map_name:
            return self._fail(
                goal_handle,
                "No map is currently loaded. Load a map first via the UI.",
            )

        self.get_logger().info(f"Current map: '{map_name}'")

        # ── 2. Resolve coordinates via map service ─────────────────────
        self._pub_feedback(
            goal_handle,
            f"Looking up '{point_name}' on map '{map_name}'...",
            0.05,
        )

        if not self._get_point_client.wait_for_service(timeout_sec=5.0):
            return self._fail(
                goal_handle,
                "map_manager/get_point_coordinates service not available",
            )

        req = GetPointCoordinates.Request()
        req.map_name = map_name
        req.point_name = point_name

        coord_future = self._get_point_client.call_async(req)
        coord_event = threading.Event()
        coord_future.add_done_callback(lambda _: coord_event.set())
        coord_event.wait(timeout=10.0)

        if not coord_future.done() or coord_future.result() is None:
            return self._fail(
                goal_handle,
                f"Timed out waiting for coordinates of '{point_name}'",
            )

        coord_resp = coord_future.result()
        if not coord_resp.success:
            return self._fail(goal_handle, coord_resp.message)

        x, y = coord_resp.x, coord_resp.y
        self.get_logger().info(
            f"Resolved '{point_name}' on '{map_name}' -> (x={x:.4f}, y={y:.4f})"
        )

        # ── 3. Wait for Nav2 action server ─────────────────────────────
        self._pub_feedback(
            goal_handle,
            f"Waiting for Nav2... target ({x:.2f}, {y:.2f})",
            0.08,
        )

        if not self._nav_client.wait_for_server(timeout_sec=10.0):
            return self._fail(
                goal_handle,
                "/navigate_to_pose action server not available — is Nav2 running?",
            )

        # ── 4. Build and send NavigateToPose goal ──────────────────────
        nav_goal = NavigateToPose.Goal()
        nav_goal.pose = PoseStamped()
        nav_goal.pose.header.frame_id = "map"
        nav_goal.pose.header.stamp = self.get_clock().now().to_msg()
        nav_goal.pose.pose.position.x = x
        nav_goal.pose.pose.position.y = y
        nav_goal.pose.pose.orientation.w = 1.0

        self._initial_dist = None
        self._current_goal_handle = goal_handle

        send_goal_event = threading.Event()
        send_goal_future = self._nav_client.send_goal_async(
            nav_goal,
            feedback_callback=self._nav_feedback_cb,
        )
        send_goal_future.add_done_callback(lambda _: send_goal_event.set())
        send_goal_event.wait(timeout=15.0)

        if not send_goal_future.done() or send_goal_future.result() is None:
            return self._fail(goal_handle, "Timed out sending goal to Nav2")

        nav_goal_handle = send_goal_future.result()
        if not nav_goal_handle.accepted:
            self.get_logger().error(
                f"Nav2 REJECTED goal: point='{point_name}', map='{map_name}', "
                f"x={x:.4f}, y={y:.4f}. "
                f"Is bt_navigator in 'active' state? Has SLAM published /map?"
            )
            return self._fail(goal_handle, "Nav2 rejected the navigation goal")

        self._pub_feedback(
            goal_handle,
            f"Navigating to '{point_name}' at ({x:.2f}, {y:.2f})",
            0.10,
        )

        # ── 5. Wait for Nav2 result, supporting cancellation ───────────
        result_event = threading.Event()
        result_future = nav_goal_handle.get_result_async()
        result_future.add_done_callback(lambda _: result_event.set())

        while not result_event.is_set():
            if goal_handle.is_cancel_requested:
                self.get_logger().info("Cancelling Nav2 goal on skill cancel request")
                cancel_future = nav_goal_handle.cancel_goal_async()
                cancel_event = threading.Event()
                cancel_future.add_done_callback(lambda _: cancel_event.set())
                cancel_event.wait(timeout=5.0)
                result = ExecuteSkill.Result()
                result.success = False
                result.result_json = json.dumps({"status": "cancelled"})
                result.error_message = "Navigation cancelled"
                goal_handle.canceled()
                return result
            result_event.wait(timeout=0.1)

        nav_result = result_future.result()
        status = nav_result.status

        if status == GoalStatus.STATUS_SUCCEEDED:
            self._pub_feedback(goal_handle, "Arrived!", 1.0)
            result = ExecuteSkill.Result()
            result.success = True
            result.result_json = json.dumps({
                "status": "arrived",
                "point_name": point_name,
                "map_name": map_name,
                "x": x,
                "y": y,
            })
            result.error_message = ""
            goal_handle.succeed()
            self.get_logger().info(
                f"Arrived at '{point_name}' on map '{map_name}'"
            )
            return result

        error_msg = f"Nav2 navigation failed — status code {status}"
        self.get_logger().warn(error_msg)
        return self._fail(goal_handle, error_msg)

    # ------------------------------------------------------------------
    # Nav2 feedback → skill feedback
    # ------------------------------------------------------------------

    def _nav_feedback_cb(self, feedback_msg):
        nav_fb = feedback_msg.feedback
        dist = nav_fb.distance_remaining

        if self._initial_dist is None:
            self._initial_dist = max(float(dist), 0.01)

        progress = max(0.0, min(0.95, 1.0 - float(dist) / self._initial_dist))
        self._pub_feedback(
            self._current_goal_handle,
            f"Navigating... {dist:.2f} m remaining",
            progress,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pub_feedback(goal_handle, status: str, progress: float):
        fb = ExecuteSkill.Feedback()
        fb.status = status
        fb.progress = float(max(0.0, min(1.0, progress)))
        goal_handle.publish_feedback(fb)

    @staticmethod
    def _fail(goal_handle, message: str) -> ExecuteSkill.Result:
        result = ExecuteSkill.Result()
        result.success = False
        result.result_json = json.dumps({"status": "failed", "error": message})
        result.error_message = message
        goal_handle.abort()
        return result


def main(args=None):
    rclpy.init(args=args)
    node = GoToPointServer()
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
