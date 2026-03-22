"""gesture_server.py — Robot gesture tool for physical expressions.

Allows the agent to perform physical gestures like waving, nodding, etc.
Currently a mock that logs the gesture — replace with actual servo/motor
commands for your hardware.
"""

import time
from dataclasses import dataclass

import rclpy
from rclpy.executors import MultiThreadedExecutor

from andr_msgs.action import ExecuteSkill
from andr_tools import BaseAgentTool


class GestureTool(BaseAgentTool):
    TOOL_NAME = "gesture"
    TOOL_DESCRIPTION = (
        "Perform a physical gesture or body language expression. "
        "Use this to wave back at people, nod, shake head, point, or make "
        "other social gestures. Combine with 'speak' for a natural response."
    )
    TOOL_PARAMETERS = [
        {
            "name": "gesture_type",
            "type": "string",
            "required": True,
            "description": (
                "The gesture to perform. Options: wave, nod, shake_head, "
                "point, bow, shrug, thumbs_up"
            ),
        },
        {
            "name": "intensity",
            "type": "string",
            "required": False,
            "description": "How pronounced the gesture should be: subtle, normal, enthusiastic",
        },
    ]
    TOOL_CATEGORY = "expression"
    TOOL_TAGS = ["gesture", "social", "body_language", "wave"]

    SUPPORTED_GESTURES = {
        "wave", "nod", "shake_head", "point", "bow", "shrug", "thumbs_up",
    }

    @dataclass
    class ParamsType:
        gesture_type: str
        intensity: str = "normal"

    def _execute(self, params: ParamsType, goal_handle) -> dict:
        gesture = params.gesture_type.lower().strip()
        intensity = params.intensity.lower().strip() if params.intensity else "normal"

        if gesture not in self.SUPPORTED_GESTURES:
            return {
                "status": "error",
                "message": (
                    f"Unknown gesture '{gesture}'. "
                    f"Supported: {', '.join(sorted(self.SUPPORTED_GESTURES))}"
                ),
            }

        self.get_logger().info(
            f"[MOCK GESTURE] Performing '{gesture}' (intensity={intensity})"
        )

        # Simulate gesture execution with feedback
        feedback = ExecuteSkill.Feedback()
        feedback.status = f"performing_{gesture}"
        feedback.progress = 0.3
        goal_handle.publish_feedback(feedback)

        # TODO: Replace with actual hardware commands
        # e.g., publish to servo controller, send joint trajectory, etc.
        duration = {"subtle": 0.5, "normal": 1.0, "enthusiastic": 1.5}.get(intensity, 1.0)
        time.sleep(duration)

        feedback.status = "done"
        feedback.progress = 1.0
        goal_handle.publish_feedback(feedback)

        self.get_logger().info(f"[MOCK GESTURE] Completed '{gesture}'")
        return {
            "status": "done",
            "gesture": gesture,
            "intensity": intensity,
            "duration_s": duration,
        }


def main(args=None):
    rclpy.init(args=args)
    node = GestureTool()
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
