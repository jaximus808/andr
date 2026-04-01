"""Web UI input source.

Serves the robot dashboard at http://localhost:<port> and bridges
browser WebSocket messages to the ANDR agent pipeline via ROS 2.

Extends BaseInputSource so that browser prompts flow through the
standard task_manager pipeline:

  Browser -> WebSocket -> self.send_task() -> task_manager -> agent
  Agent feedback -> on_task_feedback() / on_task_completed() -> WebSocket -> browser

Also subscribes to ROS topics for visualization (map, scan, odom)
and proxies services (map_manager, agent config, prompt manager).

Run standalone:  python -m inputs.web_ui
Or let start.py auto-discover it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import queue
import threading
import time
import zlib
from typing import Set

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from std_msgs.msg import String
from nav_msgs.msg import OccupancyGrid, Odometry
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import PoseWithCovarianceStamped

from andr_msgs.msg import RobotSpeech
from andr_msgs.srv import (
    SaveMap, SavePoint, GetMapPoints, GetMaps,
    SetSlamConfig, GetSlamConfig, RestartSlam,
    GetAgentConfig, SetAgentConfig,
    GetSystemPrompt, SetSystemPrompt, GetPromptHistory,
    ListTools,
)

from andr import BaseInputSource


class WebUIInput(BaseInputSource):
    """BaseInputSource that serves the web dashboard and bridges browser commands to the agent."""

    SOURCE_NAME = "web_ui"
    SOURCE_DESCRIPTION = "Web dashboard — bridges browser prompts to the agent"

    def __init__(self):
        super().__init__()

        self._event_queue: queue.Queue = queue.Queue()
        self._port = int(os.environ.get("ANDR_UI_PORT", "8080"))

        # ── Topic subscribers (visualization + status) ────────────────
        self.create_subscription(RobotSpeech, "/robot/speech", self._on_speech, 10)
        self.create_subscription(String, "/agent/feedback", self._on_agent_feedback, 10)
        self.create_subscription(String, "/robot/status", self._on_robot_status, 10)

        map_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
            history=HistoryPolicy.KEEP_LAST, depth=1,
        )
        self.create_subscription(OccupancyGrid, "/map", self._on_map, map_qos)
        self.create_subscription(LaserScan, "/scan", self._on_scan, 10)
        self.create_subscription(Odometry, "/odom", self._on_odom, 10)
        self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self._on_amcl_pose, 10)

        # ── Throttle state ────────────────────────────────────────────
        self._last_map_send = 0.0
        self._last_scan_send = 0.0
        self._last_odom_send = 0.0

        # ── Service clients (map, agent config, prompt, tools) ────────
        self._save_map_client = self.create_client(SaveMap, "map_manager/save_map")
        self._save_point_client = self.create_client(SavePoint, "map_manager/save_point")
        self._get_points_client = self.create_client(GetMapPoints, "map_manager/get_map_points")
        self._get_maps_client = self.create_client(GetMaps, "map_manager/get_maps")
        self._set_slam_config_client = self.create_client(SetSlamConfig, "map_manager/set_slam_config")
        self._get_slam_config_client = self.create_client(GetSlamConfig, "map_manager/get_slam_config")
        self._restart_slam_client = self.create_client(RestartSlam, "map_manager/restart_slam")
        self._get_agent_config_client = self.create_client(GetAgentConfig, "agent/get_config")
        self._set_agent_config_client = self.create_client(SetAgentConfig, "agent/set_config")
        self._get_prompt_client = self.create_client(GetSystemPrompt, "prompt_manager/get_system_prompt")
        self._set_prompt_client = self.create_client(SetSystemPrompt, "prompt_manager/set_system_prompt")
        self._get_history_client = self.create_client(GetPromptHistory, "prompt_manager/get_prompt_history")
        self._list_tools_client = self.create_client(ListTools, "tool_manager/list")

        # ── Node discovery timer ──────────────────────────────────────
        self._discovery_timer = self.create_timer(5.0, self._discover_nodes)

        self.get_logger().info(f"WebUIInput ready — serving on port {self._port}")

    # ==================================================================
    # BaseInputSource hooks — relay task lifecycle to WebSocket
    # ==================================================================

    def on_task_accepted(self, prompt: str) -> None:
        self._push({"type": "task_accepted", "text": "Task accepted by agent."})

    def on_task_rejected(self, prompt: str) -> None:
        self._push({"type": "error", "text": "Task was rejected."})

    def on_task_feedback(self, state: str, status: str, progress: float) -> None:
        self._push({"type": "task_feedback", "state": state, "status": status, "progress": progress})

    def on_task_completed(self, prompt: str, success: bool, summary: str) -> None:
        self._push({"type": "task_result", "success": success, "summary": summary})
        self._push({"type": "robot_speech", "text": summary, "emotion": "satisfied" if success else "concerned"})

    # ==================================================================
    # Push helper (ROS thread -> async web server)
    # ==================================================================
    def _push(self, event: dict) -> None:
        self._event_queue.put_nowait(event)

    # ==================================================================
    # Topic callbacks (visualization + status)
    # ==================================================================
    def _on_speech(self, msg):
        self._push({"type": "robot_speech", "text": msg.text, "emotion": msg.emotion})

    def _on_agent_feedback(self, msg):
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            data = {"raw": msg.data}
        self._push({"type": "agent_feedback", **data})

    def _on_robot_status(self, msg):
        self._push({"type": "robot_status", "text": msg.data})

    def _on_map(self, msg):
        now = time.monotonic()
        if now - self._last_map_send < 2.0:
            return
        self._last_map_send = now

        raw_bytes = bytes([(v + 128) & 0xFF for v in msg.data])
        compressed = zlib.compress(raw_bytes, level=6)
        b64_data = base64.b64encode(compressed).decode("ascii")

        self._push({
            "type": "map_data",
            "width": msg.info.width,
            "height": msg.info.height,
            "resolution": msg.info.resolution,
            "origin_x": msg.info.origin.position.x,
            "origin_y": msg.info.origin.position.y,
            "data_b64": b64_data,
        })

    def _on_scan(self, msg):
        now = time.monotonic()
        if now - self._last_scan_send < 0.2:
            return
        self._last_scan_send = now

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

    def _on_odom(self, msg):
        now = time.monotonic()
        if now - self._last_odom_send < 0.1:
            return
        self._last_odom_send = now

        pos = msg.pose.pose.position
        ori = msg.pose.pose.orientation
        siny_cosp = 2.0 * (ori.w * ori.z + ori.x * ori.y)
        cosy_cosp = 1.0 - 2.0 * (ori.y * ori.y + ori.z * ori.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        self._push({
            "type": "robot_pose",
            "x": pos.x, "y": pos.y, "yaw": yaw,
            "vx": msg.twist.twist.linear.x,
            "wz": msg.twist.twist.angular.z,
        })

    def _on_amcl_pose(self, msg):
        pos = msg.pose.pose.position
        ori = msg.pose.pose.orientation
        siny_cosp = 2.0 * (ori.w * ori.z + ori.x * ori.y)
        cosy_cosp = 1.0 - 2.0 * (ori.y * ori.y + ori.z * ori.z)
        yaw = math.atan2(siny_cosp, cosy_cosp)
        self._push({"type": "robot_pose", "x": pos.x, "y": pos.y, "yaw": yaw, "source": "amcl"})

    # ==================================================================
    # Service proxies (map, agent config, prompt, tools)
    # ==================================================================
    def save_map(self, map_name: str):
        if not self._save_map_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "save_map_result", "success": False, "message": "Service not available"})
            return
        req = SaveMap.Request()
        req.map_name = map_name
        future = self._save_map_client.call_async(req)
        future.add_done_callback(lambda f: self._relay(f, "save_map_result", refresh_maps=True))

    def save_point(self, map_name: str, label: str, x: float, y: float):
        if not self._save_point_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "poi_result", "success": False, "message": "Service not available"})
            return
        req = SavePoint.Request()
        req.map_name = map_name
        req.label = label
        req.x = x
        req.y = y
        future = self._save_point_client.call_async(req)
        future.add_done_callback(lambda f: self._relay(f, "poi_result"))

    def get_points(self, map_name: str):
        if not self._get_points_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "poi_list", "success": False, "message": "Service not available", "points": []})
            return
        req = GetMapPoints.Request()
        req.map_name = map_name
        future = self._get_points_client.call_async(req)

        def _cb(f):
            try:
                res = f.result()
                points = [{"label": res.labels[i], "x": res.x[i], "y": res.y[i]} for i in range(len(res.labels))] if res.success else []
                self._push({"type": "poi_list", "success": res.success, "message": res.message, "points": points})
            except Exception as e:
                self._push({"type": "poi_list", "success": False, "message": str(e), "points": []})
        future.add_done_callback(_cb)

    def get_maps(self):
        if not self._get_maps_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "map_list", "success": False, "maps": []})
            return
        future = self._get_maps_client.call_async(GetMaps.Request())

        def _cb(f):
            try:
                res = f.result()
                self._push({"type": "map_list", "success": True, "maps": list(res.map_names)})
            except Exception as e:
                self._push({"type": "map_list", "success": False, "message": str(e), "maps": []})
        future.add_done_callback(_cb)

    def set_slam_config(self, map_name: str, localization: bool):
        if not self._set_slam_config_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "slam_config_result", "success": False, "message": "Service not available"})
            return
        req = SetSlamConfig.Request()
        req.map_name = map_name
        req.localization = localization
        future = self._set_slam_config_client.call_async(req)
        future.add_done_callback(lambda f: self._relay(f, "slam_config_result"))

    def get_slam_config(self):
        if not self._get_slam_config_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "slam_config", "success": False, "map_name": "", "localization": False})
            return
        future = self._get_slam_config_client.call_async(GetSlamConfig.Request())

        def _cb(f):
            try:
                res = f.result()
                self._push({"type": "slam_config", "success": res.success, "map_name": res.map_name, "localization": res.localization, "message": res.message})
            except Exception as e:
                self._push({"type": "slam_config", "success": False, "map_name": "", "localization": False, "message": str(e)})
        future.add_done_callback(_cb)

    def restart_slam(self):
        if not self._restart_slam_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "restart_slam_result", "success": False, "message": "Service not available"})
            return
        future = self._restart_slam_client.call_async(RestartSlam.Request())
        future.add_done_callback(lambda f: self._relay(f, "restart_slam_result"))

    def get_agent_config(self):
        if not self._get_agent_config_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "agent_config", "success": False, "message": "Service not available"})
            return
        future = self._get_agent_config_client.call_async(GetAgentConfig.Request())

        def _cb(f):
            try:
                res = f.result()
                self._push({
                    "type": "agent_config", "success": True,
                    "llm_backend": res.llm_backend, "llm_model": res.llm_model,
                    "llm_host": res.llm_host, "llm_temperature": res.llm_temperature,
                    "max_iterations": res.max_iterations,
                    "memory_backend": res.memory_backend, "memory_top_k": res.memory_top_k,
                })
            except Exception as e:
                self._push({"type": "agent_config", "success": False, "message": str(e)})
        future.add_done_callback(_cb)

    def set_agent_config(self, config: dict):
        if not self._set_agent_config_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "agent_config_result", "success": False, "message": "Service not available"})
            return
        req = SetAgentConfig.Request()
        req.llm_backend = str(config.get("llm_backend", ""))
        req.llm_model = str(config.get("llm_model", ""))
        req.llm_host = str(config.get("llm_host", ""))
        req.llm_temperature = float(config.get("llm_temperature", -1.0))
        req.max_iterations = int(config.get("max_iterations", -1))
        future = self._set_agent_config_client.call_async(req)

        def _cb(f):
            try:
                res = f.result()
                self._push({"type": "agent_config_result", "success": res.success, "message": res.message})
                if res.success:
                    self.get_agent_config()
            except Exception as e:
                self._push({"type": "agent_config_result", "success": False, "message": str(e)})
        future.add_done_callback(_cb)

    def get_system_prompt(self):
        if not self._get_prompt_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "system_prompt", "success": False, "message": "Service not available"})
            return
        future = self._get_prompt_client.call_async(GetSystemPrompt.Request())

        def _cb(f):
            try:
                res = f.result()
                self._push({"type": "system_prompt", "success": res.success, "prompt": res.prompt, "version": res.version, "timestamp": res.timestamp})
            except Exception as e:
                self._push({"type": "system_prompt", "success": False, "message": str(e)})
        future.add_done_callback(_cb)

    def set_system_prompt(self, prompt: str):
        if not self._set_prompt_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "set_prompt_result", "success": False, "message": "Service not available"})
            return
        req = SetSystemPrompt.Request()
        req.prompt = prompt
        future = self._set_prompt_client.call_async(req)

        def _cb(f):
            try:
                res = f.result()
                self._push({"type": "set_prompt_result", "success": res.success, "message": res.message, "version": res.version})
            except Exception as e:
                self._push({"type": "set_prompt_result", "success": False, "message": str(e)})
        future.add_done_callback(_cb)

    def get_prompt_history(self):
        if not self._get_history_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "prompt_history", "success": False, "entries": []})
            return
        future = self._get_history_client.call_async(GetPromptHistory.Request())

        def _cb(f):
            try:
                res = f.result()
                entries = [{"version": res.versions[i], "prompt": res.prompts[i], "timestamp": res.timestamps[i]} for i in range(len(res.versions))]
                self._push({"type": "prompt_history", "success": res.success, "entries": entries})
            except Exception as e:
                self._push({"type": "prompt_history", "success": False, "message": str(e), "entries": []})
        future.add_done_callback(_cb)

    def get_tools(self):
        if not self._list_tools_client.wait_for_service(timeout_sec=2.0):
            self._push({"type": "tools_list", "success": False, "tools": []})
            return
        future = self._list_tools_client.call_async(ListTools.Request())

        def _cb(f):
            try:
                res = f.result()
                tools = []
                for i, name in enumerate(res.tool_names):
                    params = []
                    if i < len(res.parameters_json) and res.parameters_json[i]:
                        try:
                            params = json.loads(res.parameters_json[i])
                        except json.JSONDecodeError:
                            pass
                    tools.append({
                        "name": name,
                        "description": res.descriptions[i] if i < len(res.descriptions) else "",
                        "category": res.categories[i] if i < len(res.categories) else "general",
                        "action_server": res.action_servers[i] if i < len(res.action_servers) else "",
                        "parameters": params,
                    })
                self._push({"type": "tools_list", "success": True, "tools": tools})
            except Exception as e:
                self._push({"type": "tools_list", "success": False, "message": str(e), "tools": []})
        future.add_done_callback(_cb)

    # ==================================================================
    # Node discovery
    # ==================================================================
    def _discover_nodes(self):
        node_names = self.get_node_names_and_namespaces()
        nodes = [{"name": n, "namespace": ns} for n, ns in node_names]
        topic_list = self.get_topic_names_and_types()
        actions = sorted({t.rsplit("/_action/status", 1)[0] for t, _ in topic_list if t.endswith("/_action/status")})
        self._push({"type": "node_status", "nodes": nodes, "action_servers": actions})

    # ==================================================================
    # Helpers
    # ==================================================================
    def _relay(self, future, event_type, refresh_maps=False):
        try:
            res = future.result()
            self._push({"type": event_type, "success": res.success, "message": res.message})
            if refresh_maps and res.success:
                self.get_maps()
        except Exception as e:
            self._push({"type": event_type, "success": False, "message": str(e)})


# ======================================================================
# FastAPI web server (runs in a background thread)
# ======================================================================

def _build_app(node: WebUIInput):
    """Create the FastAPI app wired to the given ROS node."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="ANDR UI")
    clients: Set[WebSocket] = set()

    # Resolve static directory (relative to this file -> ../ui/static)
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui", "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        index_path = os.path.join(static_dir, "index.html")
        if os.path.isfile(index_path):
            with open(index_path) as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>ANDR UI</h1><p>Static files not found.</p>")

    @app.get("/rviz", response_class=HTMLResponse)
    async def rviz():
        rviz_path = os.path.join(static_dir, "rviz.html")
        if os.path.isfile(rviz_path):
            with open(rviz_path) as f:
                return HTMLResponse(content=f.read())
        return HTMLResponse(content="<h1>RViz</h1><p>Static files not found.</p>")

    @app.on_event("startup")
    async def startup():
        asyncio.create_task(_broadcast_loop())

    async def _broadcast_loop():
        """Pull events from the ROS node's queue and send to all browsers."""
        while True:
            batch = []
            try:
                while True:
                    batch.append(node._event_queue.get_nowait())
            except queue.Empty:
                pass

            if batch:
                dead = []
                for event in batch:
                    payload = json.dumps(event)
                    for ws in list(clients):
                        try:
                            await ws.send_text(payload)
                        except Exception:
                            dead.append(ws)
                for ws in dead:
                    clients.discard(ws)

            await asyncio.sleep(0.05)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        clients.add(ws)
        await ws.send_text(json.dumps({"type": "connected", "text": "Connected to ANDR UI"}))

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_text(json.dumps({"type": "error", "text": "Invalid JSON"}))
                    continue

                msg_type = msg.get("type")

                if msg_type == "prompt":
                    text = str(msg.get("text", "")).strip()
                    context = str(msg.get("context", ""))
                    priority = int(msg.get("priority", 5))
                    if text:
                        # send_task is inherited from BaseInputSource
                        node.send_task(text, context, priority=priority)
                        payload = json.dumps({"type": "user_prompt", "text": text})
                        for c in list(clients):
                            try:
                                await c.send_text(payload)
                            except Exception:
                                pass

                elif msg_type == "save_map":
                    node.save_map(str(msg.get("map_name", "")))
                elif msg_type == "save_point":
                    node.save_point(str(msg.get("map_name", "")), str(msg.get("label", "")),
                                    float(msg.get("x", 0.0)), float(msg.get("y", 0.0)))
                elif msg_type == "get_points":
                    node.get_points(str(msg.get("map_name", "")))
                elif msg_type == "get_maps":
                    node.get_maps()
                elif msg_type == "set_slam_config":
                    node.set_slam_config(str(msg.get("map_name", "")), bool(msg.get("localization", False)))
                elif msg_type == "get_slam_config":
                    node.get_slam_config()
                elif msg_type == "restart_slam":
                    node.restart_slam()
                elif msg_type == "get_agent_config":
                    node.get_agent_config()
                elif msg_type == "set_agent_config":
                    node.set_agent_config(msg.get("config", {}))
                elif msg_type == "get_system_prompt":
                    node.get_system_prompt()
                elif msg_type == "set_system_prompt":
                    node.set_system_prompt(str(msg.get("prompt", "")))
                elif msg_type == "get_prompt_history":
                    node.get_prompt_history()
                elif msg_type == "get_tools":
                    node.get_tools()

        except WebSocketDisconnect:
            clients.discard(ws)

    return app


# ======================================================================
# Entry point
# ======================================================================

def main(args=None):
    """Start the Web UI input source.

    Initialises rclpy, creates the BaseInputSource node, starts the
    FastAPI server in a background thread, then spins the node.
    """
    rclpy.init(args=args)
    node = WebUIInput()

    app = _build_app(node)

    def _run_server():
        import uvicorn
        host = os.environ.get("ANDR_UI_HOST", "0.0.0.0")
        uvicorn.run(app, host=host, port=node._port, log_level="info")

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()

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
