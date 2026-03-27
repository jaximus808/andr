"""Spin tool — rotates the robot in place by publishing to /cmd_vel.

Publishes geometry_msgs/Twist at 20 Hz for the requested duration.
"""

import time
from dataclasses import dataclass

import rclpy
from rclpy.executors import MultiThreadedExecutor
from geometry_msgs.msg import Twist

from andr_msgs.action import ExecuteSkill
from andr import BaseAgentTool


class SpinTool(BaseAgentTool):
    TOOL_NAME = "spin"
    TOOL_DESCRIPTION = "Rotate the robot in place for a given duration"
    TOOL_PARAMETERS = [
        {"name": "duration_s", "type": "float", "required": False,
         "description": "How long to spin in seconds (default 3.0)"},
        {"name": "speed_deg_s", "type": "float", "required": False,
         "description": "Angular speed in degrees/second (default 90)"},
        {"name": "direction", "type": "string", "required": False,
         "description": "Spin direction: left or right (default left)"},
    ]
    TOOL_CATEGORY = "movement"
    TOOL_TAGS = ["rotation", "movement"]

    @dataclass
    class ParamsType:
        duration_s: float = 3.0
        speed_deg_s: float = 90.0
        direction: str = "left"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cmd_vel_pub = self.create_publisher(Twist, "cmd_vel", 10)

    def _execute(self, params: ParamsType, goal_handle) -> dict:
        duration_s = params.duration_s
        speed_deg_s = params.speed_deg_s
        direction = params.direction

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

        rate_hz = 20.0
        total_ticks = int(duration_s * rate_hz)
        tick_period = 1.0 / rate_hz

        for i in range(1, total_ticks + 1):
            if goal_handle.is_cancel_requested:
                self._cmd_vel_pub.publish(Twist())
                return {"status": "cancelled"}

            self._cmd_vel_pub.publish(twist)

            if i % int(rate_hz / 2) == 0 or i == total_ticks:
                feedback = ExecuteSkill.Feedback()
                feedback.status = f"spinning {direction} ({i}/{total_ticks})"
                feedback.progress = float(i) / total_ticks
                goal_handle.publish_feedback(feedback)

            time.sleep(tick_period)

        # Stop the robot
        self._cmd_vel_pub.publish(Twist())

        total_deg = speed_deg_s * duration_s
        self.get_logger().info(
            f"[SPIN] Done — rotated {direction} ~{total_deg:.0f} deg over {duration_s}s"
        )
        return {
            "status": "done",
            "direction": direction,
            "duration_s": duration_s,
            "total_rotation_deg": total_deg,
        }


def main(args=None):
    rclpy.init(args=args)
    node = SpinTool()
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
