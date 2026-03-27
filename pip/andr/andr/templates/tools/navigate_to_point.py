"""navigate_to_point tool — navigates to a named point on the current map.

Flow:
  1. Call map_manager/get_slam_config to get the currently loaded map.
  2. Call map_manager/get_point_coordinates to resolve (x, y).
  3. Send a NavigateToPose goal to Nav2 (/navigate_to_pose).
  4. Forward Nav2 feedback (distance_remaining) as ExecuteSkill progress.
  5. Return success/failure result.
"""

import threading
from dataclasses import dataclass

import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose

from andr_msgs.action import ExecuteSkill
from andr_msgs.srv import GetPointCoordinates, GetSlamConfig
from andr import BaseAgentTool


class NavigateToPointTool(BaseAgentTool):
    TOOL_NAME = "navigate_to_point"
    TOOL_DESCRIPTION = (
        "Navigate to a named point on the currently loaded map. "
        "Automatically determines the active map from SLAM config."
    )
    TOOL_PARAMETERS = [
        {"name": "point_name", "type": "string", "required": True,
         "description": "Name of the saved point to navigate to"},
    ]
    TOOL_CATEGORY = "navigation"
    TOOL_TAGS = ["navigation", "movement", "waypoint"]

    @dataclass
    class ParamsType:
        point_name: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self._get_slam_config_client = self.create_client(
            GetSlamConfig,
            "map_manager/get_slam_config",
            callback_group=self._cb_group,
        )

        self._get_point_client = self.create_client(
            GetPointCoordinates,
            "map_manager/get_point_coordinates",
            callback_group=self._cb_group,
        )

        self._nav_client = ActionClient(
            self,
            NavigateToPose,
            "/navigate_to_pose",
            callback_group=self._cb_group,
        )

        self._initial_dist = None
        self._current_goal_handle = None

    def _execute(self, params: ParamsType, goal_handle) -> dict:
        point_name = params.point_name.strip()
        if not point_name:
            raise ValueError("'point_name' is required")

        # 1. Get the current map from SLAM config
        self._pub_feedback(goal_handle, "Getting current map...", 0.0)

        if not self._get_slam_config_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError("map_manager/get_slam_config service not available")

        config_future = self._get_slam_config_client.call_async(GetSlamConfig.Request())
        config_event = threading.Event()
        config_future.add_done_callback(lambda _: config_event.set())
        config_event.wait(timeout=10.0)

        if not config_future.done() or config_future.result() is None:
            raise RuntimeError("Timed out getting SLAM config")

        config_resp = config_future.result()
        if not config_resp.success:
            raise RuntimeError(f"SLAM config error: {config_resp.message}")

        map_name = config_resp.map_name
        if not map_name:
            raise RuntimeError("No map is currently loaded. Load a map first via the UI.")

        self.get_logger().info(f"Current map: '{map_name}'")

        # 2. Resolve coordinates via map service
        self._pub_feedback(goal_handle, f"Looking up '{point_name}' on map '{map_name}'...", 0.05)

        if not self._get_point_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError("map_manager/get_point_coordinates service not available")

        req = GetPointCoordinates.Request()
        req.map_name = map_name
        req.point_name = point_name

        coord_future = self._get_point_client.call_async(req)
        coord_event = threading.Event()
        coord_future.add_done_callback(lambda _: coord_event.set())
        coord_event.wait(timeout=10.0)

        if not coord_future.done() or coord_future.result() is None:
            raise RuntimeError(f"Timed out waiting for coordinates of '{point_name}'")

        coord_resp = coord_future.result()
        if not coord_resp.success:
            raise RuntimeError(coord_resp.message)

        x, y = coord_resp.x, coord_resp.y
        self.get_logger().info(f"Resolved '{point_name}' on '{map_name}' -> ({x:.3f}, {y:.3f})")

        # 3. Wait for Nav2 action server
        self._pub_feedback(goal_handle, f"Waiting for Nav2... target ({x:.2f}, {y:.2f})", 0.02)

        if not self._nav_client.wait_for_server(timeout_sec=10.0):
            raise RuntimeError("/navigate_to_pose action server not available — is Nav2 running?")

        # 4. Build and send NavigateToPose goal
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
            raise RuntimeError("Timed out sending goal to Nav2")

        nav_goal_handle = send_goal_future.result()
        if not nav_goal_handle.accepted:
            raise RuntimeError("Nav2 rejected the navigation goal")

        self._pub_feedback(goal_handle, f"Navigating to '{point_name}' at ({x:.2f}, {y:.2f})", 0.05)

        # 5. Wait for Nav2 result, supporting cancellation
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
                return {"status": "cancelled", "point_name": point_name}
            result_event.wait(timeout=0.1)

        nav_result = result_future.result()
        status = nav_result.status

        if status == GoalStatus.STATUS_SUCCEEDED:
            self._pub_feedback(goal_handle, "Arrived!", 1.0)
            self.get_logger().info(f"Arrived at '{point_name}' on map '{map_name}'")
            return {
                "status": "arrived",
                "point_name": point_name,
                "map_name": map_name,
                "x": x,
                "y": y,
            }

        raise RuntimeError(f"Nav2 navigation failed — status code {status}")

    # ------------------------------------------------------------------
    # Nav2 feedback -> skill feedback
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


def main(args=None):
    rclpy.init(args=args)
    node = NavigateToPointTool()
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
