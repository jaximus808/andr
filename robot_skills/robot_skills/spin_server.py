"""Spin action server — rotates the robot in place for a given duration.

Publishes geometry_msgs/Twist on /cmd_vel to spin the robot in Gazebo.
"""

import json
import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node
from geometry_msgs.msg import Twist

from andr.action import ExecuteSkill


class SpinServer(Node):
    def __init__(self):
        super().__init__("spin_server")
        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            "/skills/spin",
            execute_callback=self._execute_cb,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
        )
        self._cmd_vel_pub = self.create_publisher(Twist, "cmd_vel", 10)
        self.get_logger().info("SpinServer ready on '/skills/spin'")

    def _goal_cb(self, goal_request) -> GoalResponse:
        self.get_logger().info(
            f"Spin goal received: {goal_request.params_json}"
        )
        return GoalResponse.ACCEPT

    def _cancel_cb(self, goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _execute_cb(self, goal_handle) -> ExecuteSkill.Result:
        params = json.loads(goal_handle.request.params_json or "{}")
        duration_s = float(params.get("duration_s", 3.0))
        speed_deg_s = float(params.get("speed_deg_s", 90.0))
        direction = params.get("direction", "left")

        # Convert deg/s to rad/s; negate for clockwise (right)
        angular_vel = speed_deg_s * 3.14159265 / 180.0
        if direction == "right":
            angular_vel = -angular_vel

        self.get_logger().info(
            f"[SPIN] direction='{direction}' duration={duration_s}s "
            f"speed={speed_deg_s} deg/s"
        )

        twist = Twist()
        twist.angular.z = angular_vel

        # Publish cmd_vel at ~20 Hz for the requested duration
        rate_hz = 20.0
        total_ticks = int(duration_s * rate_hz)
        tick_period = 1.0 / rate_hz

        for i in range(1, total_ticks + 1):
            if goal_handle.is_cancel_requested:
                # Stop the robot
                self._cmd_vel_pub.publish(Twist())
                result = ExecuteSkill.Result()
                result.success = False
                result.result_json = json.dumps({"status": "cancelled"})
                result.error_message = "Spin cancelled"
                goal_handle.canceled()
                return result

            self._cmd_vel_pub.publish(twist)

            # Publish feedback every ~0.5 s
            if i % int(rate_hz / 2) == 0 or i == total_ticks:
                feedback = ExecuteSkill.Feedback()
                feedback.status = f"spinning {direction} ({i}/{total_ticks})"
                feedback.progress = float(i) / total_ticks
                goal_handle.publish_feedback(feedback)

            time.sleep(tick_period)

        # Stop the robot
        self._cmd_vel_pub.publish(Twist())

        total_deg = speed_deg_s * duration_s
        result = ExecuteSkill.Result()
        result.success = True
        result.result_json = json.dumps({
            "status": "done",
            "direction": direction,
            "duration_s": duration_s,
            "total_rotation_deg": total_deg,
        })
        result.error_message = ""

        self.get_logger().info(
            f"[SPIN] Done — rotated {direction} ~{total_deg:.0f}° "
            f"over {duration_s}s"
        )
        goal_handle.succeed()
        return result


def main(args=None):
    rclpy.init(args=args)
    node = SpinServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
