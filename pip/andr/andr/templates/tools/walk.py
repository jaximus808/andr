"""Walk tool — moves the robot forward or backward by publishing to /cmd_vel.

Publishes geometry_msgs/Twist at 20 Hz for the requested duration.
"""

import time
from dataclasses import dataclass

import rclpy
from rclpy.executors import MultiThreadedExecutor
from geometry_msgs.msg import Twist

from andr_msgs.action import ExecuteSkill
from andr import BaseAgentTool


class WalkTool(BaseAgentTool):
    TOOL_NAME = "walk"
    TOOL_DESCRIPTION = "Walk forward or backward for a given duration"
    TOOL_PARAMETERS = [
        {"name": "direction", "type": "string", "required": False,
         "description": "Walk direction: forward or backward (default forward)"},
        {"name": "duration_s", "type": "float", "required": False,
         "description": "How long to walk in seconds (default 2.0)"},
        {"name": "speed", "type": "float", "required": False,
         "description": "Walking speed in m/s (default 0.5)"},
    ]
    TOOL_CATEGORY = "movement"
    TOOL_TAGS = ["locomotion", "movement"]

    @dataclass
    class ParamsType:
        direction: str = "forward"
        duration_s: float = 2.0
        speed: float = 0.5

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._cmd_vel_pub = self.create_publisher(Twist, "cmd_vel", 10)

    def _execute(self, params: ParamsType, goal_handle) -> dict:
        duration_s = params.duration_s
        speed = params.speed
        direction = params.direction

        linear_vel = speed if direction == "forward" else -speed

        self.get_logger().info(
            f"[WALK] direction='{direction}' duration={duration_s}s speed={speed} m/s"
        )

        twist = Twist()
        twist.linear.x = linear_vel

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
                feedback.status = f"walking {direction} ({i}/{total_ticks})"
                feedback.progress = float(i) / total_ticks
                goal_handle.publish_feedback(feedback)

            time.sleep(tick_period)

        # Stop the robot
        self._cmd_vel_pub.publish(Twist())

        distance_m = speed * duration_s
        self.get_logger().info(
            f"[WALK] Done — walked {direction} ~{distance_m:.2f}m over {duration_s}s"
        )
        return {
            "status": "done",
            "direction": direction,
            "distance_m": distance_m,
            "duration_s": duration_s,
        }


def main(args=None):
    rclpy.init(args=args)
    node = WalkTool()
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
