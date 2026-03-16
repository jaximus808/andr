"""Mock walk action server — placeholder for locomotion hardware interface."""

import json
import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node

from andr.action import ExecuteSkill


class WalkServer(Node):
    def __init__(self):
        super().__init__("walk_server")
        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            "/skills/walk",
            execute_callback=self._execute_cb,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
        )
        self.get_logger().info("WalkServer ready on '/skills/walk'")

    def _goal_cb(self, goal_request) -> GoalResponse:
        self.get_logger().info(
            f"Walk goal received: {goal_request.params_json}"
        )
        return GoalResponse.ACCEPT

    def _cancel_cb(self, goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _execute_cb(self, goal_handle) -> ExecuteSkill.Result:
        params = json.loads(goal_handle.request.params_json or "{}")
        direction = params.get("direction", "forward")
        duration_s = params.get("duration_s", 2.0)
        speed = params.get("speed", 0.5)

        self.get_logger().info(
            f"[MOCK WALK] direction='{direction}' duration={duration_s}s speed={speed}"
        )

        # Simulate walking with progress updates
        steps = 5
        step_time = duration_s / steps
        for i in range(1, steps + 1):
            if goal_handle.is_cancel_requested:
                result = ExecuteSkill.Result()
                result.success = False
                result.result_json = json.dumps({"status": "cancelled"})
                result.error_message = "Walk cancelled"
                goal_handle.canceled()
                return result

            feedback = ExecuteSkill.Feedback()
            feedback.status = f"walking {direction} ({i}/{steps})"
            feedback.progress = float(i) / steps
            goal_handle.publish_feedback(feedback)
            time.sleep(step_time)

        result = ExecuteSkill.Result()
        result.success = True
        result.result_json = json.dumps({
            "status": "done",
            "direction": direction,
            "distance_m": speed * duration_s,
            "duration_s": duration_s,
        })
        result.error_message = ""

        self.get_logger().info(
            f"[MOCK WALK] Done — walked {direction} for {duration_s}s"
        )
        goal_handle.succeed()
        return result


def main(args=None):
    rclpy.init(args=args)
    node = WalkServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
