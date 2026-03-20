"""skills.py — Skills registry and ROS 2 action-client executor for ANDR."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SkillParameter:
    name: str
    type: str = "string"
    required: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "SkillParameter":
        return cls(
            name=d.get("name", ""),
            type=d.get("type", "string"),
            required=bool(d.get("required", True)),
            description=d.get("description", ""),
        )


@dataclass
class Skill:
    name: str
    description: str
    parameters: list[SkillParameter] = field(default_factory=list)
    returns: str = "void"
    category: str = "general"
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "Skill":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", "").strip(),
            parameters=[
                SkillParameter.from_dict(p) for p in d.get("parameters", [])
            ],
            returns=d.get("returns", "void"),
            category=d.get("category", "general"),
            tags=d.get("tags", []),
        )

    def to_prompt_line(self) -> str:
        """One-line summary suitable for embedding in an LLM prompt."""
        params = ", ".join(
            f"{p.name}: {p.type}{'*' if p.required else '?'}"
            for p in self.parameters
        )
        return (
            f"  {self.name}({params}) -> {self.returns}\n"
            f"    {self.description}"
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class SkillsRegistry:
    """Holds available robot skills and formats them for LLM injection."""

    def __init__(self, skills: Optional[list[Skill]] = None):
        self._skills: dict[str, Skill] = {}
        for skill in (skills or []):
            self._skills[skill.name] = skill

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_dict_list(cls, raw: list[dict]) -> "SkillsRegistry":
        """Build a registry directly from a list of dicts (useful in tests)."""
        return cls([Skill.from_dict(d) for d in raw])

    @classmethod
    def from_tool_manager(cls, node, timeout_sec: float = 10.0) -> "SkillsRegistry":
        """Discover tools dynamically by calling the tool_manager/list service."""
        import json as _json

        try:
            from andr.srv import ListTools  # noqa: PLC0415
        except ImportError:
            logger.warning("ListTools service type not available — using empty registry.")
            return cls()

        client = node.create_client(ListTools, "tool_manager/list")
        if not client.wait_for_service(timeout_sec=timeout_sec):
            logger.warning(
                "tool_manager/list service not available after %.1fs — using empty registry.",
                timeout_sec,
            )
            return cls()

        req = ListTools.Request()
        future = client.call_async(req)

        # Block until result
        event = threading.Event()
        future.add_done_callback(lambda _: event.set())
        if not event.wait(timeout=timeout_sec):
            logger.warning("tool_manager/list timed out — using empty registry.")
            return cls()

        res = future.result()
        skills = []
        for i, name in enumerate(res.tool_names):
            params_raw = _json.loads(res.parameters_json[i]) if i < len(res.parameters_json) else []
            skills.append(Skill(
                name=name,
                description=res.descriptions[i] if i < len(res.descriptions) else "",
                parameters=[SkillParameter.from_dict(p) for p in params_raw],
                category=res.categories[i] if i < len(res.categories) else "general",
            ))

        logger.info("Discovered %d tool(s) from tool_manager.", len(skills))
        return cls(skills)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[Skill]:
        return self._skills.get(name)

    def all_skills(self) -> list[Skill]:
        return list(self._skills.values())

    def by_category(self, category: str) -> list[Skill]:
        return [s for s in self._skills.values() if s.category == category]

    def names(self) -> list[str]:
        return list(self._skills.keys())

    def __len__(self) -> int:
        return len(self._skills)

    # ------------------------------------------------------------------
    # Prompt formatting
    # ------------------------------------------------------------------

    def to_prompt_block(self, category: Optional[str] = None) -> str:
        """Format all (or category-filtered) skills as a text block for LLM injection."""
        skills = (
            self.by_category(category) if category else self.all_skills()
        )
        if not skills:
            return "AVAILABLE SKILLS\n================\n(none)"

        lines = ["AVAILABLE SKILLS", "================"]
        for skill in skills:
            lines.append(skill.to_prompt_line())
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill Executor — sends goals to the /skill_executor action server
# ---------------------------------------------------------------------------

class SkillExecutor:
    """
    Dispatches skill calls to the ``/tool_manager/execute`` ROS 2 node via ExecuteSkill actions.

    Parameters
    ----------
    registry:
        Loaded SkillsRegistry used for pre-call validation.
    node:
        rclpy.node.Node that owns this executor (action client is attached to it).
    action_server_name:
        ROS 2 action name of the tool_manager execute endpoint.
    timeout_s:
        Server availability timeout and per-call timeout in seconds.
    """

    ACTION_NAME = "/tool_manager/execute"

    def __init__(
        self,
        registry: SkillsRegistry,
        node,                          # rclpy.node.Node — avoids circular import
        action_server_name: str = "/tool_manager/execute",
        timeout_s: float = 30.0,
    ):
        self._registry    = registry
        self._node        = node
        self._timeout_s   = timeout_s
        self._action_name = action_server_name

        # Lazy import so the module is importable without a live ROS context
        # (e.g. unit tests that only use SkillsRegistry).
        try:
            from rclpy.action import ActionClient          # noqa: PLC0415
            from andr.action import ExecuteSkill           # noqa: PLC0415
            self._ActionClient  = ActionClient
            self._ExecuteSkill  = ExecuteSkill
            self._action_client = ActionClient(node, ExecuteSkill, action_server_name)
            logger.info(
                "SkillExecutor: action client created for '%s'.", action_server_name
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SkillExecutor: could not create action client (%s). "
                "All skill calls will fail until the action server is available.",
                exc,
            )
            self._action_client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, skill_name: str, args: Optional[dict] = None) -> str:
        """
        Send an ExecuteSkill goal and block until a result is returned.
        Returns a string injected into the LLM conversation; errors start with 'ERROR:'.
        """
        args = args or {}

        # --- Registry validation ---
        skill = self._registry.get(skill_name)
        if skill is None:
            err = (
                f"Skill '{skill_name}' is not in the registry. "
                f"Available: {self._registry.names()}"
            )
            logger.warning("SkillExecutor: %s", err)
            return f"ERROR: {err}"

        missing = [
            p.name for p in skill.parameters
            if p.required and p.name not in args
        ]
        if missing:
            err = f"Skill '{skill_name}' is missing required args: {missing}"
            logger.warning("SkillExecutor: %s", err)
            return f"ERROR: {err}"

        # --- Action client unavailable → error ---
        if self._action_client is None:
            err = "skill_executor action client is not available."
            logger.error("SkillExecutor: %s", err)
            return f"ERROR: {err}"

        # --- Send goal to skill_executor node ---
        return self._send_goal(skill_name, args)

    def _send_goal(self, skill_name: str, args: dict) -> str:
        """Block until the skill_executor node returns a result."""
        import json as _json

        if not self._action_client.wait_for_server(timeout_sec=self._timeout_s):
            err = f"skill_executor '{self._action_name}' not available after {self._timeout_s}s."
            logger.error("SkillExecutor: %s", err)
            return f"ERROR: {err}"

        goal_msg = self._ExecuteSkill.Goal()
        goal_msg.skill_name  = skill_name
        goal_msg.params_json = _json.dumps(args)
        logger.info("SkillExecutor: sending skill='%s' params=%s", skill_name, goal_msg.params_json)

        send_future = self._action_client.send_goal_async(goal_msg)
        send_event = threading.Event()
        send_future.add_done_callback(lambda _: send_event.set())
        if not send_event.wait(timeout=self._timeout_s):
            err = f"Skill goal '{skill_name}' send timed out after {self._timeout_s}s."
            logger.error("SkillExecutor: %s", err)
            return f"ERROR: {err}"

        goal_handle = send_future.result()
        if goal_handle is None or not goal_handle.accepted:
            err = f"Skill goal '{skill_name}' was rejected by skill_executor."
            logger.error("SkillExecutor: %s", err)
            return f"ERROR: {err}"

        # Wait for the result — no timeout, skills can run for a long time
        result_future = goal_handle.get_result_async()
        result_event = threading.Event()
        result_future.add_done_callback(lambda _: result_event.set())
        result_event.wait()

        wrapped = result_future.result()
        if wrapped is None:
            err = f"Skill '{skill_name}' returned no result."
            logger.error("SkillExecutor: %s", err)
            return f"ERROR: {err}"

        res = wrapped.result
        if not res.success:
            err = f"Skill '{skill_name}' failed: {res.error_message}"
            logger.warning("SkillExecutor: %s", err)
            return f"ERROR: {err}"

        logger.info(
            "SkillExecutor: '%s' succeeded → %s", skill_name, res.result_json[:120]
        )
        return res.result_json

