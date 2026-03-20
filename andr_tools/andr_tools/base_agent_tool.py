"""base_agent_tool.py — Base class for self-registering agent tools.

Subclass this to create a tool that automatically registers with the
ToolManager on startup and deregisters on shutdown.

Example
-------
::

    from dataclasses import dataclass
    from andr_tools import BaseAgentTool

    class SpeakTool(BaseAgentTool):
        TOOL_NAME = "speak"
        TOOL_DESCRIPTION = "Text-to-speech via robot speaker"
        TOOL_PARAMETERS = [
            {"name": "text", "type": "string", "required": True,
             "description": "Sentence to speak"},
        ]
        TOOL_CATEGORY = "communication"
        TOOL_TAGS = ["tts", "speech"]

        @dataclass
        class ParamsType:
            text: str
            voice: str = "default"

        def _execute(self, params, goal_handle):
            self.get_logger().info(f"Speaking: {params.text}")
            return {"status": "done", "text_spoken": params.text}
"""

from __future__ import annotations

import json
import dataclasses
import logging
from abc import abstractmethod
from typing import Any, ClassVar, Optional

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from andr.action import ExecuteSkill
from andr.srv import RegisterTool, DeregisterTool

logger = logging.getLogger(__name__)


class BaseAgentTool(Node):
    """Base ROS 2 node that hosts an ExecuteSkill action server and
    self-registers with the ToolManager.

    Subclasses **must** define:
        TOOL_NAME          — unique tool identifier (snake_case)
        TOOL_DESCRIPTION   — human-readable description
        TOOL_PARAMETERS    — list of param dicts [{name, type, required, description}]
        _execute(params, goal_handle) — implementation returning a dict result

    Subclasses **may** define:
        TOOL_CATEGORY      — category string (default "general")
        TOOL_TAGS          — list of tag strings (default [])
        ParamsType         — dataclass for typed parameter conversion
    """

    # ── Class-level config (override in subclass) ─────────────────────────
    TOOL_NAME: ClassVar[str] = ""
    TOOL_DESCRIPTION: ClassVar[str] = ""
    TOOL_PARAMETERS: ClassVar[list[dict]] = []
    TOOL_CATEGORY: ClassVar[str] = "general"
    TOOL_TAGS: ClassVar[list[str]] = []

    # Optional: subclass can define a dataclass for typed params
    ParamsType: ClassVar[Optional[type]] = None

    def __init__(self, **kwargs: Any):
        if not self.TOOL_NAME:
            raise ValueError("TOOL_NAME must be set in subclass")

        node_name = f"{self.TOOL_NAME}_tool"
        super().__init__(node_name, **kwargs)

        self._action_server_name = f"/tools/{self.TOOL_NAME}"
        self._cb_group = ReentrantCallbackGroup()

        # ── Action server ─────────────────────────────────────────────────
        self._action_server = ActionServer(
            self,
            ExecuteSkill,
            self._action_server_name,
            execute_callback=self._execute_cb,
            goal_callback=self._goal_cb,
            cancel_callback=self._cancel_cb,
            callback_group=self._cb_group,
        )

        # ── Service clients for tool_manager ──────────────────────────────
        self._register_client = self.create_client(
            RegisterTool, "tool_manager/register",
            callback_group=self._cb_group,
        )
        self._deregister_client = self.create_client(
            DeregisterTool, "tool_manager/deregister",
            callback_group=self._cb_group,
        )

        self.get_logger().info(
            f"BaseAgentTool '{self.TOOL_NAME}' ready on '{self._action_server_name}'"
        )

        # Register with tool_manager
        self._register_with_manager()

    # ── Registration ──────────────────────────────────────────────────────

    def _register_with_manager(self) -> None:
        """Call tool_manager/register to announce this tool."""
        if not self._register_client.wait_for_service(timeout_sec=10.0):
            self.get_logger().warn(
                "tool_manager/register service not available — "
                "tool will operate standalone until manager is up."
            )
            return

        req = RegisterTool.Request()
        req.tool_name = self.TOOL_NAME
        req.description = self.TOOL_DESCRIPTION
        req.action_server = self._action_server_name
        req.parameters_json = json.dumps(self.TOOL_PARAMETERS)
        req.category = self.TOOL_CATEGORY
        req.tags = self.TOOL_TAGS

        future = self._register_client.call_async(req)
        future.add_done_callback(self._on_register_done)

    def _on_register_done(self, future) -> None:
        try:
            res = future.result()
            if res.success:
                self.get_logger().info(
                    f"Registered with tool_manager: {res.message}"
                )
            else:
                self.get_logger().warn(
                    f"Registration failed: {res.message}"
                )
        except Exception as exc:
            self.get_logger().error(f"Registration service call failed: {exc}")

    def _deregister_from_manager(self) -> None:
        """Call tool_manager/deregister to remove this tool."""
        if not self._deregister_client.service_is_ready():
            return

        req = DeregisterTool.Request()
        req.tool_name = self.TOOL_NAME

        future = self._deregister_client.call_async(req)
        future.add_done_callback(self._on_deregister_done)

    def _on_deregister_done(self, future) -> None:
        try:
            res = future.result()
            if res.success:
                self.get_logger().info(
                    f"Deregistered from tool_manager: {res.message}"
                )
            else:
                self.get_logger().warn(
                    f"Deregistration failed: {res.message}"
                )
        except Exception as exc:
            self.get_logger().error(f"Deregistration service call failed: {exc}")

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def destroy_node(self) -> None:
        """Deregister from tool_manager before destroying the node."""
        self.get_logger().info(f"Shutting down tool '{self.TOOL_NAME}' — deregistering…")
        self._deregister_from_manager()
        super().destroy_node()

    # ── Action callbacks ──────────────────────────────────────────────────

    def _goal_cb(self, goal_request) -> GoalResponse:
        self.get_logger().info(
            f"Goal received: {goal_request.params_json}"
        )
        return GoalResponse.ACCEPT

    def _cancel_cb(self, goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _execute_cb(self, goal_handle) -> ExecuteSkill.Result:
        """Parse params, optionally convert to ParamsType, call _execute."""
        raw_params = json.loads(goal_handle.request.params_json or "{}")

        # Convert to typed params if ParamsType is defined
        if self.ParamsType is not None:
            params = self._convert_params(raw_params)
        else:
            params = raw_params

        try:
            result_data = self._execute(params, goal_handle)
        except Exception as exc:
            self.get_logger().error(f"Tool '{self.TOOL_NAME}' failed: {exc}")
            result = ExecuteSkill.Result()
            result.success = False
            result.result_json = "{}"
            result.error_message = str(exc)
            goal_handle.abort()
            return result

        result = ExecuteSkill.Result()
        result.success = True
        result.result_json = json.dumps(result_data) if isinstance(result_data, dict) else str(result_data)
        result.error_message = ""

        goal_handle.succeed()
        return result

    def _convert_params(self, raw: dict) -> Any:
        """Convert a raw dict to self.ParamsType (must be a dataclass)."""
        cls = self.ParamsType
        if dataclasses.is_dataclass(cls):
            # Only pass fields that exist in the dataclass
            field_names = {f.name for f in dataclasses.fields(cls)}
            filtered = {k: v for k, v in raw.items() if k in field_names}
            return cls(**filtered)
        # Fallback: try calling the type directly
        return cls(**raw)

    # ── Abstract method ───────────────────────────────────────────────────

    @abstractmethod
    def _execute(self, params: Any, goal_handle) -> dict:
        """Execute the tool logic. Return a dict that will be JSON-serialized
        as result_json. Use goal_handle.publish_feedback() for progress."""
        ...
