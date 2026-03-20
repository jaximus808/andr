"""Walk tool — placeholder for locomotion hardware interface."""

import time
from dataclasses import dataclass

import rclpy
from rclpy.executors import MultiThreadedExecutor

from andr.action import ExecuteSkill
from andr_tools import BaseAgentTool


class WalkTool(BaseAgentTool):
    TOOL_NAME = "walk"
    TOOL_DESCRIPTION = "Walk forward/backward for a duration"
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

    def _execute(self, params: ParamsType, goal_handle) -> dict:
        self.get_logger().info(
            f"[MOCK WALK] direction='{params.direction}' "
            f"duration={params.duration_s}s speed={params.speed}"
        )

        steps = 5
        step_time = params.duration_s / steps
        for i in range(1, steps + 1):
            if goal_handle.is_cancel_requested:
                return {"status": "cancelled"}

            feedback = ExecuteSkill.Feedback()
            feedback.status = f"walking {params.direction} ({i}/{steps})"
            feedback.progress = float(i) / steps
            goal_handle.publish_feedback(feedback)
            time.sleep(step_time)

        self.get_logger().info(
            f"[MOCK WALK] Done — walked {params.direction} for {params.duration_s}s"
        )
        return {
            "status": "done",
            "direction": params.direction,
            "distance_m": params.speed * params.duration_s,
            "duration_s": params.duration_s,
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
