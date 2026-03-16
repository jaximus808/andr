"""Mock speak action server — placeholder for TTS hardware interface."""

import json
import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.node import Node

from andr.action import ExecuteSkill


class SpeakServer(Node):
    def __init__(self):
        super().__init__("speak_server")
        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            "/skills/speak",
            execute_callback=self._execute_cb,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
        )
        self.get_logger().info("SpeakServer ready on '/skills/speak'")

    def _goal_cb(self, goal_request) -> GoalResponse:
        self.get_logger().info(
            f"Speak goal received: {goal_request.params_json}"
        )
        return GoalResponse.ACCEPT

    def _cancel_cb(self, goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _execute_cb(self, goal_handle) -> ExecuteSkill.Result:
        params = json.loads(goal_handle.request.params_json or "{}")
        text = params.get("text", "")
        voice = params.get("voice", "default")

        self.get_logger().info(
            f"[MOCK SPEAK] text='{text}' voice='{voice}'"
        )

        # Simulate TTS duration
        feedback = ExecuteSkill.Feedback()
        feedback.status = "speaking"
        feedback.progress = 0.5
        goal_handle.publish_feedback(feedback)
        time.sleep(0.5)

        result = ExecuteSkill.Result()
        result.success = True
        result.result_json = json.dumps({
            "status": "done",
            "text_spoken": text,
            "duration_s": 0.5,
        })
        result.error_message = ""

        self.get_logger().info(f"[MOCK SPEAK] Done — spoke: '{text}'")
        goal_handle.succeed()
        return result


def main(args=None):
    rclpy.init(args=args)
    node = SpeakServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
