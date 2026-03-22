"""Speak tool — placeholder for TTS hardware interface."""

import time
from dataclasses import dataclass

import rclpy
from rclpy.executors import MultiThreadedExecutor

from andr_msgs.action import ExecuteSkill
from andr_tools import BaseAgentTool


class SpeakTool(BaseAgentTool):
    TOOL_NAME = "speak"
    TOOL_DESCRIPTION = "Synthesise and play text via robot speaker"
    TOOL_PARAMETERS = [
        {"name": "text", "type": "string", "required": True,
         "description": "Sentence to speak (max 300 chars)"},
        {"name": "voice", "type": "string", "required": False,
         "description": "TTS voice/style (e.g. calm, cheerful)"},
    ]
    TOOL_CATEGORY = "communication"
    TOOL_TAGS = ["tts", "speech", "audio"]

    @dataclass
    class ParamsType:
        text: str
        voice: str = "default"

    def _execute(self, params: ParamsType, goal_handle) -> dict:
        self.get_logger().info(
            f"[MOCK SPEAK] text='{params.text}' voice='{params.voice}'"
        )

        # Simulate TTS duration
        feedback = ExecuteSkill.Feedback()
        feedback.status = "speaking"
        feedback.progress = 0.5
        goal_handle.publish_feedback(feedback)
        time.sleep(0.5)

        self.get_logger().info(f"[MOCK SPEAK] Done — spoke: '{params.text}'")
        return {
            "status": "done",
            "text_spoken": params.text,
            "duration_s": 0.5,
        }


def main(args=None):
    rclpy.init(args=args)
    node = SpeakTool()
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
