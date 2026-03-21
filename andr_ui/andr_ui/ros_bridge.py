"""
ros_bridge.py — ROS 2 node that:
  • Subscribes to robot status topics and pushes events to connected WebSocket clients.
  • Subscribes to visualization topics (map, scan, odom) for the 2D RViz web view.
  • Sends task goals to /task_manager/execute when the UI sends a prompt.
  • Provides save/get points-of-interest passthrough to map_manager services.
  • Periodically discovers active nodes and action servers for the status panel.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import struct
import threading
import time
import zlib
from typing import Callable

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from std_msgs.msg import String
from nav_msgs.msg import OccupancyGrid, Odometry
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import PoseWithCovarianceStamped

from andr.msg import Prompt, RobotSpeech
from andr.action import TaskGoal
from andr.srv import (
    SaveMap, SavePoint, GetMapPoints, GetMaps, SetSlamConfig, GetSlamConfig, RestartSlam,
    GetAgentConfig, SetAgentConfig,
    GetSystemPrompt, SetSystemPrompt, GetPromptHistory,
)


class RosBridgeNode(Node):
    """Lightweight ROS node that shuttles data between ROS topics and asyncio queues."""

    def __init__(self, push_event: Callable[[dict], None]):
        super().__init__("andr_ui_bridge")

        self._push = push_event

        # ── Subscribers ──────────────────────────────────────────────────
        self.create_subscription(
            RobotSpeech, "/robot/speech", self._on_robot_speech, 10,
        )
        self.create_subscription(
            String, "/agent/feedback", self._on_agent_feedback, 10,
        )
        self.create_subscription(
            String, "/robot/status", self._on_robot_status, 10,
        )

        # ── Visualization subscribers ────────────────────────────────────
        # Map uses transient-local durability so we get the last published map
        map_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.create_subscription(
            OccupancyGrid, "/map", self._on_map, map_qos,
        )

        self.create_subscription(
            LaserScan, "/scan", self._on_scan, 10,
        )

        self.create_subscription(
            Odometry, "/odom", self._on_odom, 10,
        )

        # AMCL pose (if available)
        self.create_subscription(
            PoseWithCovarianceStamped, "/amcl_pose", self._on_amcl_pose, 10,
        )

        # ── Throttle state for visualization ─────────────────────────────
        self._last_map_send = 0.0
        self._last_scan_send = 0.0
        self._last_odom_send = 0.0
        self._MAP_INTERVAL = 2.0     # send map at most every 2s
        self._SCAN_INTERVAL = 0.2    # send scan at most every 200ms
        self._ODOM_INTERVAL = 0.1    # send odom at most every 100ms

        # ── Action client to task_manager ────────────────────────────────
        self._task_client = ActionClient(self, TaskGoal, "/task_manager/execute")

        # ── Publisher (kept for backwards compat / logging) ──────────────
        self._prompt_pub = self.create_publisher(Prompt, "/ui/prompt", 10)

        # ── Service clients for map_manager ──────────────────────────────
        self._save_map_client = self.create_client(SaveMap, "map_manager/save_map")
        self._save_point_client = self.create_client(SavePoint, "map_manager/save_point")
        self._get_points_client = self.create_client(GetMapPoints, "map_manager/get_map_points")
        self._get_maps_client = self.create_client(GetMaps, "map_manager/get_maps")
        self._set_slam_config_client = self.create_client(SetSlamConfig, "map_manager/set_slam_config")
        self._get_slam_config_client = self.create_client(GetSlamConfig, "map_manager/get_slam_config")
        self._restart_slam_client = self.create_client(RestartSlam, "map_manager/restart_slam")

        # ── Service clients for agent config ──────────────────────────────
        self._get_agent_config_client = self.create_client(GetAgentConfig, "agent/get_config")
        self._set_agent_config_client = self.create_client(SetAgentConfig, "agent/set_config")

        # ── Service clients for prompt_manager ─────────────────────────
        self._get_prompt_client = self.create_client(GetSystemPrompt, "prompt_manager/get_system_prompt")
        self._set_prompt_client = self.create_client(SetSystemPrompt, "prompt_manager/set_system_prompt")
        self._get_history_client = self.create_client(GetPromptHistory, "prompt_manager/get_prompt_history")

        # ── Periodic node/action discovery (every 5s) ────────────────────
        self._discovery_timer = self.create_timer(5.0, self._discover_nodes)

        self.get_logger().info("RosBridgeNode ready (with 2D viz support)")

    # ── Incoming topic handlers ──────────────────────────────────────────

    def _on_robot_speech(self, msg: RobotSpeech) -> None:
        self._push({
            "type": "robot_speech",
            "text": msg.text,
            "emotion": msg.emotion,
        })

    def _on_agent_feedback(self, msg: String) -> None:
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            data = {"raw": msg.data}
        self._push({"type": "agent_feedback", **data})

    def _on_robot_status(self, msg: String) -> None:
        self._push({"type": "robot_status", "text": msg.data})

    # ── Visualization topic handlers ─────────────────────────────────────

    def _on_map(self, msg: OccupancyGrid) -> None:
        now = time.monotonic()
        if now - self._last_map_send < self._MAP_INTERVAL:
            return
        self._last_map_send = now

        width = msg.info.width
        height = msg.info.height
        resolution = msg.info.resolution
        origin_x = msg.info.origin.position.x
        origin_y = msg.info.origin.position.y

        # Compress raw occupancy data (int8 values: -1, 0..100)
        raw_bytes = bytes([(v + 128) & 0xFF for v in msg.data])
        compressed = zlib.compress(raw_bytes, level=6)
        b64_data = base64.b64encode(compressed).decode("ascii")

        self._push({
            "type": "map_data",
            "width": width,
            "height": height,
            "resolution": resolution,
            "origin_x": origin_x,
            "origin_y": origin_y,
            "data_b64": b64_data,
        })

    def _on_scan(self, msg: LaserScan) -> None:
        now = time.monotonic()
        if now - self._last_scan_send < self._SCAN_INTERVAL:
            return
        self._last_scan_send = now

        # Downsample scan to reduce payload (every 4th ray)
        step = 4
        ranges = msg.ranges[::step]

        self._push({
            "type": "scan_data",
            "angle_min": msg.angle_min,
            "angle_max": msg.angle_max,
            "angle_increment": msg.angle_increment * step,
            "range_min": msg.range_min,
            "range_max": msg.range_max,
            "ranges": [r if math.isfinite(r) else -1.0 for r in ranges],
        })

    def _on_odom(self, msg: Odometry) -> None:
        now = time.monotonic()
        if now - self._last_odom_send < self._ODOM_INTERVAL:
            return
        self._last_odom_send = now

        pos = msg.pose.pose.position
        ori = msg.pose.pose.orientation
        # Convert quaternion to yaw
        siny_cosp = 2.0 * (ori.w * ori.z + ori.x * ori.y)
        cosy_cosp = 1.0 - 2.0 * (ori.y * ori.y + ori.z * ori.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        self._push({
            "type": "robot_pose",
            "x": pos.x,
            "y": pos.y,
            "yaw": yaw,
            "vx": msg.twist.twist.linear.x,
            "wz": msg.twist.twist.angular.z,
        })

    def _on_amcl_pose(self, msg: PoseWithCovarianceStamped) -> None:
        pos = msg.pose.pose.position
        ori = msg.pose.pose.orientation
        siny_cosp = 2.0 * (ori.w * ori.z + ori.x * ori.y)
        cosy_cosp = 1.0 - 2.0 * (ori.y * ori.y + ori.z * ori.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        self._push({
            "type": "robot_pose",
            "x": pos.x,
            "y": pos.y,
            "yaw": yaw,
            "source": "amcl",
        })

    # ── Task submission (action client) ──────────────────────────────────

    def send_task(self, prompt: str, context: str = "") -> None:
        """Send a task to the task_manager action server. Non-blocking."""
        # Also publish on topic for logging
        pub_msg = Prompt()
        pub_msg.prompt = prompt
        pub_msg.context = context
        pub_msg.stamp = self.get_clock().now().to_msg()
        self._prompt_pub.publish(pub_msg)

        if not self._task_client.wait_for_server(timeout_sec=2.0):
            self.get_logger().warn("task_manager not available — prompt published on topic only.")
            self._push({
                "type": "error",
                "text": "Task manager not available. Is task_manager_server running?",
            })
            return

        goal = TaskGoal.Goal()
        goal.prompt = prompt
        goal.context = context

        self.get_logger().info(f"Sending task to task_manager: '{prompt[:80]}'")
        self._task_client.send_goal_async(
            goal,
            feedback_callback=self._on_task_feedback,
        ).add_done_callback(self._on_task_goal_response)

    def _on_task_goal_response(self, future) -> None:
        goal_handle = future.result()
        if not goal_handle.accepted:
            self._push({"type": "error", "text": "Task was rejected by task_manager."})
            return

        self.get_logger().info("Task accepted by task_manager.")
        goal_handle.get_result_async().add_done_callback(self._on_task_result)

    def _on_task_feedback(self, feedback_msg) -> None:
        fb = feedback_msg.feedback
        self._push({
            "type": "task_feedback",
            "state": fb.state,
            "status": fb.status,
            "progress": fb.progress,
        })

    def _on_task_result(self, future) -> None:
        wrapped = future.result()
        if wrapped is None:
            self._push({"type": "error", "text": "Task timed out."})
            return
        res = wrapped.result
        self._push({
            "type": "task_result",
            "success": res.success,
            "summary": res.summary,
        })
        # Also show as a robot speech bubble
        self._push({
            "type": "robot_speech",
            "text": res.summary,
            "emotion": "satisfied" if res.success else "concerned",
        })

    # ── Map saving ─────────────────────────────────────────────────────────

    def save_map(self, map_name: str) -> None:
        """Save the current SLAM map via map_manager service."""
        if not self._save_map_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "save_map_result", "success": False,
                         "message": "map_manager/save_map service not available"})
            return

        req = SaveMap.Request()
        req.map_name = map_name

        future = self._save_map_client.call_async(req)
        future.add_done_callback(self._on_save_map_result)

    def _on_save_map_result(self, future) -> None:
        try:
            res = future.result()
            self._push({
                "type": "save_map_result",
                "success": res.success,
                "message": res.message,
            })
            # Refresh map list after successful save
            if res.success:
                self.get_maps()
        except Exception as e:
            self._push({"type": "save_map_result", "success": False, "message": str(e)})

    # ── Points of interest ───────────────────────────────────────────────

    def save_point(self, map_name: str, label: str, x: float, y: float) -> None:
        """Save a point of interest via map_manager service."""
        if not self._save_point_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "poi_result", "success": False,
                         "message": "map_manager/save_point service not available"})
            return

        req = SavePoint.Request()
        req.map_name = map_name
        req.label = label
        req.x = x
        req.y = y

        future = self._save_point_client.call_async(req)
        future.add_done_callback(self._on_save_point_result)

    def _on_save_point_result(self, future) -> None:
        try:
            res = future.result()
            self._push({
                "type": "poi_result",
                "success": res.success,
                "message": res.message,
            })
        except Exception as e:
            self._push({"type": "poi_result", "success": False, "message": str(e)})

    def get_points(self, map_name: str) -> None:
        """Get all POIs for a map via map_manager service."""
        if not self._get_points_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "poi_list", "success": False,
                         "message": "map_manager/get_map_points service not available"})
            return

        req = GetMapPoints.Request()
        req.map_name = map_name

        future = self._get_points_client.call_async(req)
        future.add_done_callback(self._on_get_points_result)

    def _on_get_points_result(self, future) -> None:
        try:
            res = future.result()
            points = []
            if res.success:
                for i in range(len(res.labels)):
                    points.append({
                        "label": res.labels[i],
                        "x": res.x[i],
                        "y": res.y[i],
                    })
            self._push({
                "type": "poi_list",
                "success": res.success,
                "message": res.message,
                "points": points,
            })
        except Exception as e:
            self._push({"type": "poi_list", "success": False,
                         "message": str(e), "points": []})

    def get_maps(self) -> None:
        """Get list of saved maps."""
        if not self._get_maps_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "map_list", "success": False,
                         "message": "map_manager/get_maps service not available",
                         "maps": []})
            return

        req = GetMaps.Request()
        future = self._get_maps_client.call_async(req)
        future.add_done_callback(self._on_get_maps_result)

    def _on_get_maps_result(self, future) -> None:
        try:
            res = future.result()
            self._push({
                "type": "map_list",
                "success": True,
                "maps": list(res.map_names),
            })
        except Exception as e:
            self._push({"type": "map_list", "success": False,
                         "message": str(e), "maps": []})

    # ── SLAM config ──────────────────────────────────────────────────────

    def set_slam_config(self, map_name: str, localization: bool) -> None:
        """Persist SLAM map selection and mode."""
        if not self._set_slam_config_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "slam_config_result", "success": False,
                         "message": "map_manager/set_slam_config not available"})
            return

        req = SetSlamConfig.Request()
        req.map_name = map_name
        req.localization = localization

        future = self._set_slam_config_client.call_async(req)
        future.add_done_callback(self._on_set_slam_config_result)

    def _on_set_slam_config_result(self, future) -> None:
        try:
            res = future.result()
            self._push({"type": "slam_config_result", "success": res.success, "message": res.message})
        except Exception as e:
            self._push({"type": "slam_config_result", "success": False, "message": str(e)})

    def get_slam_config(self) -> None:
        """Retrieve current SLAM config and push to UI."""
        if not self._get_slam_config_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "slam_config", "success": False,
                         "map_name": "", "localization": False,
                         "message": "map_manager/get_slam_config not available"})
            return

        future = self._get_slam_config_client.call_async(GetSlamConfig.Request())
        future.add_done_callback(self._on_get_slam_config_result)

    def _on_get_slam_config_result(self, future) -> None:
        try:
            res = future.result()
            self._push({
                "type": "slam_config",
                "success": res.success,
                "map_name": res.map_name,
                "localization": res.localization,
                "message": res.message,
            })
        except Exception as e:
            self._push({"type": "slam_config", "success": False,
                         "map_name": "", "localization": False, "message": str(e)})

    def restart_slam(self) -> None:
        """Restart SLAM Toolbox with the stored config."""
        if not self._restart_slam_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "restart_slam_result", "success": False,
                         "message": "map_manager/restart_slam not available"})
            return

        future = self._restart_slam_client.call_async(RestartSlam.Request())
        future.add_done_callback(self._on_restart_slam_result)

    def _on_restart_slam_result(self, future) -> None:
        try:
            res = future.result()
            self._push({"type": "restart_slam_result", "success": res.success, "message": res.message})
        except Exception as e:
            self._push({"type": "restart_slam_result", "success": False, "message": str(e)})

    # ── Agent config ─────────────────────────────────────────────────

    def get_agent_config(self) -> None:
        """Fetch current agent config and push to UI."""
        if not self._get_agent_config_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "agent_config", "success": False,
                         "message": "agent/get_config service not available"})
            return

        future = self._get_agent_config_client.call_async(GetAgentConfig.Request())
        future.add_done_callback(self._on_get_agent_config_result)

    def _on_get_agent_config_result(self, future) -> None:
        try:
            res = future.result()
            self._push({
                "type": "agent_config",
                "success": True,
                "llm_backend": res.llm_backend,
                "llm_model": res.llm_model,
                "llm_host": res.llm_host,
                "llm_temperature": res.llm_temperature,
                "max_iterations": res.max_iterations,
                "memory_backend": res.memory_backend,
                "memory_top_k": res.memory_top_k,
            })
        except Exception as e:
            self._push({"type": "agent_config", "success": False, "message": str(e)})

    def set_agent_config(self, config: dict) -> None:
        """Update agent config via service call."""
        if not self._set_agent_config_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "agent_config_result", "success": False,
                         "message": "agent/set_config service not available"})
            return

        req = SetAgentConfig.Request()
        req.llm_backend = str(config.get("llm_backend", ""))
        req.llm_model = str(config.get("llm_model", ""))
        req.llm_host = str(config.get("llm_host", ""))
        req.llm_temperature = float(config.get("llm_temperature", -1.0))
        req.max_iterations = int(config.get("max_iterations", -1))

        future = self._set_agent_config_client.call_async(req)
        future.add_done_callback(self._on_set_agent_config_result)

    def _on_set_agent_config_result(self, future) -> None:
        try:
            res = future.result()
            self._push({
                "type": "agent_config_result",
                "success": res.success,
                "message": res.message,
            })
            # Refresh config after update
            if res.success:
                self.get_agent_config()
        except Exception as e:
            self._push({"type": "agent_config_result", "success": False, "message": str(e)})

    # ── Prompt management ──────────────────────────────────────────────

    def get_system_prompt(self) -> None:
        """Fetch current system prompt and push to UI."""
        if not self._get_prompt_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "system_prompt", "success": False,
                         "message": "prompt_manager not available"})
            return

        future = self._get_prompt_client.call_async(GetSystemPrompt.Request())
        future.add_done_callback(self._on_get_prompt_result)

    def _on_get_prompt_result(self, future) -> None:
        try:
            res = future.result()
            self._push({
                "type": "system_prompt",
                "success": res.success,
                "prompt": res.prompt,
                "version": res.version,
                "timestamp": res.timestamp,
            })
        except Exception as e:
            self._push({"type": "system_prompt", "success": False, "message": str(e)})

    def set_system_prompt(self, prompt: str) -> None:
        """Set a new system prompt."""
        if not self._set_prompt_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "set_prompt_result", "success": False,
                         "message": "prompt_manager not available"})
            return

        req = SetSystemPrompt.Request()
        req.prompt = prompt

        future = self._set_prompt_client.call_async(req)
        future.add_done_callback(self._on_set_prompt_result)

    def _on_set_prompt_result(self, future) -> None:
        try:
            res = future.result()
            self._push({
                "type": "set_prompt_result",
                "success": res.success,
                "message": res.message,
                "version": res.version,
            })
        except Exception as e:
            self._push({"type": "set_prompt_result", "success": False, "message": str(e)})

    def get_prompt_history(self) -> None:
        """Fetch prompt version history and push to UI."""
        if not self._get_history_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "prompt_history", "success": False,
                         "message": "prompt_manager not available"})
            return

        future = self._get_history_client.call_async(GetPromptHistory.Request())
        future.add_done_callback(self._on_get_history_result)

    def _on_get_history_result(self, future) -> None:
        try:
            res = future.result()
            entries = []
            for i in range(len(res.versions)):
                entries.append({
                    "version": res.versions[i],
                    "prompt": res.prompts[i],
                    "timestamp": res.timestamps[i],
                })
            self._push({
                "type": "prompt_history",
                "success": res.success,
                "entries": entries,
            })
        except Exception as e:
            self._push({"type": "prompt_history", "success": False,
                         "message": str(e), "entries": []})

    # ── Node / action server discovery ───────────────────────────────────

    def _discover_nodes(self) -> None:
        """Push a snapshot of active nodes and action servers to the UI."""
        node_names = self.get_node_names_and_namespaces()
        nodes = [
            {"name": name, "namespace": ns}
            for name, ns in node_names
        ]

        # Discover action servers via topic conventions (*/_action/status)
        topic_list = self.get_topic_names_and_types()
        action_servers = set()
        for topic_name, _ in topic_list:
            if topic_name.endswith("/_action/status"):
                action_name = topic_name.rsplit("/_action/status", 1)[0]
                action_servers.add(action_name)

        self._push({
            "type": "node_status",
            "nodes": nodes,
            "action_servers": sorted(action_servers),
        })


# ── Spin ROS in a background thread ─────────────────────────────────────

def start_ros_thread(push_event: Callable[[dict], None]) -> RosBridgeNode:
    """Initialise rclpy and spin the bridge node in a daemon thread."""
    rclpy.init()
    node = RosBridgeNode(push_event)

    def _spin():
        try:
            rclpy.spin(node)
        finally:
            node.destroy_node()
            rclpy.shutdown()

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    return node
