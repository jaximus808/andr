"""vision_server.py — VLM-powered vision node.

Subscribes to a camera image topic, periodically sends frames to a VLM
(via Ollama or OpenAI), and publishes natural-language scene descriptions.

Also registers a ``describe_scene`` tool with the ToolManager so the agent
can request an on-demand detailed scene analysis.

ROS 2 parameters
----------------
image_topic       string   /camera/image_raw   Camera topic to subscribe to.
vlm_backend       string   ollama              VLM backend: ollama | openai.
vlm_model         string   llava               Vision-language model name.
vlm_host          string   http://localhost:11434  Ollama server URL.
analyze_interval  float    3.0                 Seconds between continuous analyses.
scene_topic       string   /vision/scene       Topic to publish scene descriptions.
"""

from __future__ import annotations

import base64
import io
import json
import time
import threading
from dataclasses import dataclass
from typing import Optional

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

from std_msgs.msg import String

from andr_msgs.action import ExecuteSkill
from andr_tools import BaseAgentTool


def _encode_image_bytes(image_bytes: bytes) -> str:
    """Base64-encode raw image bytes for the VLM API."""
    return base64.b64encode(image_bytes).decode("utf-8")


def _ros_image_to_jpeg(msg) -> Optional[bytes]:
    """Convert a sensor_msgs/Image to JPEG bytes.

    Uses cv_bridge if available, otherwise falls back to raw conversion.
    """
    try:
        from cv_bridge import CvBridge
        import cv2
        bridge = CvBridge()
        cv_image = bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        _, jpeg = cv2.imencode(".jpg", cv_image, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpeg.tobytes()
    except ImportError:
        # cv_bridge not available — try raw encoding
        pass

    try:
        import numpy as np
        import cv2
        # Handle common encodings
        if msg.encoding in ("rgb8", "bgr8"):
            channels = 3
        elif msg.encoding in ("rgba8", "bgra8"):
            channels = 4
        else:
            return None

        arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(
            msg.height, msg.width, channels
        )
        if msg.encoding == "rgb8":
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        elif msg.encoding == "rgba8":
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        elif msg.encoding == "bgra8":
            arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

        _, jpeg = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpeg.tobytes()
    except Exception:
        return None


def _compressed_to_jpeg(msg) -> Optional[bytes]:
    """Extract JPEG bytes from a sensor_msgs/CompressedImage."""
    if "jpeg" in msg.format or "jpg" in msg.format:
        return bytes(msg.data)
    # Try re-encoding
    try:
        import cv2
        import numpy as np
        arr = np.frombuffer(msg.data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        _, jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return jpeg.tobytes()
    except Exception:
        return None


class VisionProcessor:
    """Stateless helper that sends an image to a VLM and returns a description."""

    def __init__(self, backend: str, model: str, host: str, logger):
        self.backend = backend
        self.model = model
        self.host = host.rstrip("/")
        self.logger = logger

    def describe(self, image_b64: str, prompt: str) -> str:
        """Send image + prompt to VLM, return text response."""
        if self.backend == "ollama":
            return self._describe_ollama(image_b64, prompt)
        elif self.backend == "openai":
            return self._describe_openai(image_b64, prompt)
        else:
            return f"Unknown VLM backend: {self.backend}"

    def _describe_ollama(self, image_b64: str, prompt: str) -> str:
        """Call Ollama's /api/generate with an image."""
        import requests
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [image_b64],
            "stream": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as exc:
            self.logger.error(f"Ollama VLM call failed: {exc}")
            return f"VLM error: {exc}"

    def _describe_openai(self, image_b64: str, prompt: str) -> str:
        """Call OpenAI's chat completions with a vision message."""
        import os
        try:
            from openai import OpenAI
        except ImportError:
            return "openai package not installed"

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                                "detail": "low",
                            },
                        },
                    ],
                }],
                max_tokens=300,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            self.logger.error(f"OpenAI VLM call failed: {exc}")
            return f"VLM error: {exc}"


# ---------------------------------------------------------------------------
# Continuous Vision Node — publishes scene descriptions on a topic
# ---------------------------------------------------------------------------

CONTINUOUS_PROMPT = (
    "Describe what you see concisely in 1-2 sentences. Focus on: "
    "people (what they are doing, gestures like waving, pointing), "
    "objects, obstacles, and anything that might require the robot's attention. "
    "If someone is waving or gesturing at the camera, mention it explicitly."
)


class VisionNode(Node):
    """Subscribes to camera images, periodically runs VLM, publishes descriptions."""

    def __init__(self):
        super().__init__("vision_node")

        # Parameters
        self.declare_parameter("image_topic", "/camera/image_raw")
        self.declare_parameter("vlm_backend", "ollama")
        self.declare_parameter("vlm_model", "llava")
        self.declare_parameter("vlm_host", "http://localhost:11434")
        self.declare_parameter("analyze_interval", 3.0)
        self.declare_parameter("scene_topic", "/vision/scene")

        image_topic = self._str("image_topic")
        self._interval = self.get_parameter("analyze_interval").get_parameter_value().double_value
        scene_topic = self._str("scene_topic")

        self._vlm = VisionProcessor(
            backend=self._str("vlm_backend"),
            model=self._str("vlm_model"),
            host=self._str("vlm_host"),
            logger=self.get_logger(),
        )

        # Publisher for scene descriptions
        self._scene_pub = self.create_publisher(String, scene_topic, 10)

        # Latest frame storage (thread-safe)
        self._latest_jpeg: Optional[bytes] = None
        self._frame_lock = threading.Lock()

        # Subscribe to camera — try both raw and compressed
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        # Try raw image first
        try:
            from sensor_msgs.msg import Image
            self._image_sub = self.create_subscription(
                Image, image_topic, self._image_cb, qos
            )
            self.get_logger().info(f"Subscribed to raw image: {image_topic}")
        except Exception:
            self.get_logger().warn(f"Could not subscribe to {image_topic}")

        # Also try compressed
        compressed_topic = image_topic.replace("/image_raw", "/image_raw/compressed")
        try:
            from sensor_msgs.msg import CompressedImage
            self._compressed_sub = self.create_subscription(
                CompressedImage, compressed_topic, self._compressed_cb, qos
            )
            self.get_logger().info(f"Subscribed to compressed: {compressed_topic}")
        except Exception:
            pass

        # Periodic analysis timer
        self._timer = self.create_timer(self._interval, self._analyze_timer_cb)
        self.get_logger().info(
            f"VisionNode ready — analyzing every {self._interval}s, "
            f"publishing to {scene_topic}"
        )

    def _str(self, name: str) -> str:
        return self.get_parameter(name).get_parameter_value().string_value

    def _image_cb(self, msg) -> None:
        jpeg = _ros_image_to_jpeg(msg)
        if jpeg:
            with self._frame_lock:
                self._latest_jpeg = jpeg

    def _compressed_cb(self, msg) -> None:
        jpeg = _compressed_to_jpeg(msg)
        if jpeg:
            with self._frame_lock:
                self._latest_jpeg = jpeg

    def _analyze_timer_cb(self) -> None:
        """Periodically send latest frame to VLM and publish description."""
        with self._frame_lock:
            jpeg = self._latest_jpeg

        if jpeg is None:
            return  # No frame yet

        image_b64 = _encode_image_bytes(jpeg)
        description = self._vlm.describe(image_b64, CONTINUOUS_PROMPT)

        if description and not description.startswith("VLM error"):
            msg = String()
            msg.data = description
            self._scene_pub.publish(msg)
            self.get_logger().info(f"Scene: {description[:120]}")

    def get_latest_jpeg(self) -> Optional[bytes]:
        """Thread-safe access to the latest camera frame."""
        with self._frame_lock:
            return self._latest_jpeg


# ---------------------------------------------------------------------------
# DescribeScene Tool — on-demand VLM analysis via BaseAgentTool
# ---------------------------------------------------------------------------

class DescribeSceneTool(BaseAgentTool):
    """Agent tool that captures the current camera frame and describes it via VLM.

    The agent calls this when it wants to actively "look" at something.
    """

    TOOL_NAME = "describe_scene"
    TOOL_DESCRIPTION = (
        "Look through the robot's camera and describe what is currently visible. "
        "Use this to understand the robot's surroundings, identify people, objects, "
        "or gestures (e.g., someone waving). You can optionally provide a specific "
        "question about the scene."
    )
    TOOL_PARAMETERS = [
        {
            "name": "question",
            "type": "string",
            "required": False,
            "description": (
                "Optional specific question about what you see "
                "(e.g., 'Is anyone waving?', 'What objects are on the table?'). "
                "If not provided, gives a general scene description."
            ),
        },
    ]
    TOOL_CATEGORY = "perception"
    TOOL_TAGS = ["vision", "camera", "vlm", "scene"]

    @dataclass
    class ParamsType:
        question: str = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.declare_parameter("vlm_backend", "ollama")
        self.declare_parameter("vlm_model", "llava")
        self.declare_parameter("vlm_host", "http://localhost:11434")
        self.declare_parameter("image_topic", "/camera/image_raw")

        self._vlm = VisionProcessor(
            backend=self._str_param("vlm_backend"),
            model=self._str_param("vlm_model"),
            host=self._str_param("vlm_host"),
            logger=self.get_logger(),
        )

        # Subscribe to camera for on-demand frame capture
        self._latest_jpeg: Optional[bytes] = None
        self._frame_lock = threading.Lock()

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )

        image_topic = self._str_param("image_topic")
        try:
            from sensor_msgs.msg import Image
            self.create_subscription(Image, image_topic, self._image_cb, qos)
            self.get_logger().info(f"DescribeSceneTool subscribed to {image_topic}")
        except Exception as exc:
            self.get_logger().warn(f"Could not subscribe to camera: {exc}")

    def _str_param(self, name: str) -> str:
        return self.get_parameter(name).get_parameter_value().string_value

    def _image_cb(self, msg) -> None:
        jpeg = _ros_image_to_jpeg(msg)
        if jpeg:
            with self._frame_lock:
                self._latest_jpeg = jpeg

    def _execute(self, params, goal_handle) -> dict:
        with self._frame_lock:
            jpeg = self._latest_jpeg

        if jpeg is None:
            return {
                "status": "error",
                "description": "No camera frame available. The camera may not be active.",
            }

        question = params.question if hasattr(params, "question") and params.question else ""

        if question:
            prompt = (
                f"Look at this image from a robot's camera and answer: {question}\n"
                "Be concise and specific."
            )
        else:
            prompt = CONTINUOUS_PROMPT

        # Publish feedback
        feedback = ExecuteSkill.Feedback()
        feedback.status = "analyzing"
        feedback.progress = 0.3
        goal_handle.publish_feedback(feedback)

        image_b64 = _encode_image_bytes(jpeg)
        description = self._vlm.describe(image_b64, prompt)

        self.get_logger().info(f"Scene description: {description[:120]}")
        return {
            "status": "done",
            "description": description,
        }


def main(args=None):
    """Launch both the continuous VisionNode and the DescribeSceneTool."""
    rclpy.init(args=args)

    vision_node = VisionNode()
    describe_tool = DescribeSceneTool()

    executor = MultiThreadedExecutor()
    executor.add_node(vision_node)
    executor.add_node(describe_tool)

    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        vision_node.destroy_node()
        describe_tool.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
