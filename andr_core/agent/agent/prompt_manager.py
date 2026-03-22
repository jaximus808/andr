"""
prompt_manager.py — ROS 2 node that manages the agent's system prompt.

Provides three services:
  - prompt_manager/get_system_prompt   → current active prompt + version
  - prompt_manager/set_system_prompt   → update prompt (old archived to history)
  - prompt_manager/get_prompt_history  → all historical prompt versions

Prompt history is persisted to a JSON file at /tmp/andr_prompt_history.json.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import List

import rclpy
from rclpy.node import Node

from andr_msgs.srv import GetSystemPrompt, SetSystemPrompt, GetPromptHistory

from .prompts.system_prompt import DEFAULT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

HISTORY_FILE = "/tmp/andr_prompt_history.json"


class PromptEntry:
    """A single prompt version."""

    def __init__(self, version: int, prompt: str, timestamp: str):
        self.version = version
        self.prompt = prompt
        self.timestamp = timestamp

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "prompt": self.prompt,
            "timestamp": self.timestamp,
        }

    @staticmethod
    def from_dict(d: dict) -> "PromptEntry":
        return PromptEntry(
            version=d["version"],
            prompt=d["prompt"],
            timestamp=d["timestamp"],
        )


class PromptManagerNode(Node):
    """Manages the active system prompt and version history."""

    def __init__(self):
        super().__init__("prompt_manager")

        self._history: List[PromptEntry] = []
        self._current: PromptEntry | None = None

        # Load persisted history or bootstrap from default
        self._load_history()

        # Services
        self.create_service(
            GetSystemPrompt,
            "prompt_manager/get_system_prompt",
            self._get_prompt_cb,
        )
        self.create_service(
            SetSystemPrompt,
            "prompt_manager/set_system_prompt",
            self._set_prompt_cb,
        )
        self.create_service(
            GetPromptHistory,
            "prompt_manager/get_prompt_history",
            self._get_history_cb,
        )

        self.get_logger().info(
            f"PromptManager ready — current version={self._current.version}, "
            f"history={len(self._history)} entries"
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_history(self) -> None:
        """Load prompt history from disk, or seed with the default prompt."""
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    data = json.load(f)
                self._history = [PromptEntry.from_dict(d) for d in data.get("history", [])]
                cur = data.get("current")
                if cur is not None:
                    self._current = PromptEntry.from_dict(cur)
                else:
                    self._current = self._history[-1] if self._history else None
            except Exception as e:
                self.get_logger().warn(f"Failed to load history: {e}")
                self._history = []
                self._current = None

        if self._current is None:
            # Bootstrap from the hardcoded default
            now = datetime.now(timezone.utc).isoformat()
            entry = PromptEntry(version=1, prompt=DEFAULT_SYSTEM_PROMPT, timestamp=now)
            self._history = [entry]
            self._current = entry
            self._persist()

    def _persist(self) -> None:
        """Write history to disk."""
        data = {
            "current": self._current.to_dict() if self._current else None,
            "history": [e.to_dict() for e in self._history],
        }
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.get_logger().error(f"Failed to persist history: {e}")

    # ------------------------------------------------------------------
    # Service callbacks
    # ------------------------------------------------------------------

    def _get_prompt_cb(self, request, response):
        if self._current is None:
            response.success = False
            response.prompt = ""
            response.version = 0
            response.timestamp = ""
        else:
            response.success = True
            response.prompt = self._current.prompt
            response.version = self._current.version
            response.timestamp = self._current.timestamp
        return response

    def _set_prompt_cb(self, request, response):
        new_prompt = request.prompt
        if not new_prompt.strip():
            response.success = False
            response.message = "Prompt cannot be empty"
            response.version = self._current.version if self._current else 0
            return response

        next_version = (self._current.version + 1) if self._current else 1
        now = datetime.now(timezone.utc).isoformat()
        entry = PromptEntry(version=next_version, prompt=new_prompt, timestamp=now)

        self._history.append(entry)
        self._current = entry
        self._persist()

        self.get_logger().info(f"System prompt updated to version {next_version}")
        response.success = True
        response.message = f"Prompt updated to version {next_version}"
        response.version = next_version
        return response

    def _get_history_cb(self, request, response):
        response.success = True
        # Return newest first
        entries = list(reversed(self._history))
        response.versions = [e.version for e in entries]
        response.prompts = [e.prompt for e in entries]
        response.timestamps = [e.timestamp for e in entries]
        return response


def main(args=None):
    logging.basicConfig(level=logging.INFO)
    rclpy.init(args=args)
    node = PromptManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
