"""base_memory_store.py — Base class for modular memory store nodes.

Subclass this to create a memory backend that exposes standard ROS 2
services for adding and querying memories.  The agent connects to these
services at startup — swap the node for a different backend without
touching any agent code.

Example
-------
::

    from andr_tools import BaseMemoryStore

    class PineconeMemory(BaseMemoryStore):
        STORE_NAME = "pinecone"

        def __init__(self):
            super().__init__()
            self._index = pinecone.Index("andr")

        def _add(self, text, metadata):
            self._index.upsert(...)

        def _query(self, query, top_k):
            results = self._index.query(...)
            return [(doc, score, meta), ...]
"""

from __future__ import annotations

import json
import logging
from abc import abstractmethod
from typing import Any, ClassVar

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from andr_msgs.srv import MemoryAdd, MemoryQuery

logger = logging.getLogger(__name__)


class BaseMemoryStore(Node):
    """Base ROS 2 node that hosts memory add/query services.

    Subclasses **must** define:
        STORE_NAME    — identifier for this backend (e.g. "chroma", "pinecone")
        _add(text, metadata) — store a document
        _query(query, top_k) — return list of (content, score, metadata_dict)

    Services exposed:
        memory/add    — MemoryAdd
        memory/query  — MemoryQuery
    """

    STORE_NAME: ClassVar[str] = ""

    def __init__(self, **kwargs: Any):
        if not self.STORE_NAME:
            raise ValueError("STORE_NAME must be set in subclass")

        node_name = f"memory_{self.STORE_NAME}"
        super().__init__(node_name, **kwargs)

        self._cb_group = ReentrantCallbackGroup()

        self._add_srv = self.create_service(
            MemoryAdd, "memory/add", self._handle_add,
            callback_group=self._cb_group,
        )
        self._query_srv = self.create_service(
            MemoryQuery, "memory/query", self._handle_query,
            callback_group=self._cb_group,
        )

        self.get_logger().info(
            f"BaseMemoryStore '{self.STORE_NAME}' ready "
            f"(services: memory/add, memory/query)"
        )

    # ── Service handlers ──────────────────────────────────────────────

    def _handle_add(self, request, response):
        try:
            metadata = json.loads(request.metadata_json) if request.metadata_json else {}
            self._add(request.text, metadata)
            response.success = True
            response.message = "Memory stored."
        except Exception as exc:
            self.get_logger().error(f"memory/add failed: {exc}")
            response.success = False
            response.message = str(exc)
        return response

    def _handle_query(self, request, response):
        try:
            results = self._query(request.query, request.top_k)
            response.contents = [r[0] for r in results]
            response.scores = [float(r[1]) for r in results]
            response.metadata_json = [json.dumps(r[2]) for r in results]
        except Exception as exc:
            self.get_logger().error(f"memory/query failed: {exc}")
            response.contents = []
            response.scores = []
            response.metadata_json = []
        return response

    # ── Abstract methods ──────────────────────────────────────────────

    @abstractmethod
    def _add(self, text: str, metadata: dict) -> None:
        """Store a document with optional metadata."""
        ...

    @abstractmethod
    def _query(self, query: str, top_k: int) -> list[tuple[str, float, dict]]:
        """Return list of (content, score_0_to_1, metadata_dict)."""
        ...
