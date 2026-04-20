"""memory.py — RAG memory store backends for ANDR.

The primary interface is ``ServiceMemoryClient``, which connects to a
standalone memory node via ROS 2 services (memory/add, memory/query).
This allows the memory backend to be swapped at startup without changing
any agent code — just run a different memory node.

``ChromaMemory`` is kept as a direct (in-process) fallback for testing
or single-node deployments.
"""

from __future__ import annotations

import abc
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import rclpy
from rclpy.node import Node

from andr_msgs.srv import MemoryAdd, MemoryQuery

logger = logging.getLogger(__name__)


@dataclass
class MemoryResult:
    content: str
    score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_prompt_line(self) -> str:
        source = self.metadata.get("source", "unknown")
        filled = round(self.score * 5)
        bar = "█" * filled + "░" * (5 - filled)
        return f"  [{bar}] ({source}): {self.content}"


class MemoryStore(abc.ABC):
    """Abstract RAG store — all backends implement this interface."""

    @abc.abstractmethod
    def add(self, text: str, metadata: Optional[dict] = None) -> None: ...

    @abc.abstractmethod
    def query(self, query: str, top_k: int = 4) -> list[MemoryResult]: ...

    def to_prompt_block(self, results: list[MemoryResult]) -> str:
        if not results:
            return "MEMORY (RAG)\n============\n(no relevant entries found)"
        lines = ["MEMORY (RAG)", "============"]
        for r in results:
            lines.append(r.to_prompt_line())
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Service-based memory client (connects to standalone memory node)
# ---------------------------------------------------------------------------

class ServiceMemoryClient(MemoryStore):
    """Connects to a memory node via ROS 2 services.

    This is the recommended path: run a separate memory node
    (e.g. ChromaMemoryNode, PineconeMemoryNode, etc.) and the agent
    talks to it through standard services.
    """

    def __init__(self, ros_node: Node, timeout_sec: float = 10.0):
        self._node = ros_node
        self._add_client = ros_node.create_client(MemoryAdd, "memory/add")
        self._query_client = ros_node.create_client(MemoryQuery, "memory/query")

        logger.info("Waiting for memory services (memory/add, memory/query)…")
        add_ok = self._add_client.wait_for_service(timeout_sec=timeout_sec)
        query_ok = self._query_client.wait_for_service(timeout_sec=timeout_sec)

        if not add_ok or not query_ok:
            raise RuntimeError(
                "Memory services not available. Make sure a memory node "
                "(e.g. memory_chroma_node) is running."
            )
        logger.info("Connected to memory services.")

    def add(self, text: str, metadata: Optional[dict] = None) -> None:
        req = MemoryAdd.Request()
        req.text = text
        req.metadata_json = json.dumps(metadata or {})

        future = self._add_client.call_async(req)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=5.0)

        res = future.result()
        if res is None:
            logger.error("memory/add call timed out")
        elif not res.success:
            logger.error("memory/add failed: %s", res.message)

    def query(self, query: str, top_k: int = 4) -> list[MemoryResult]:
        req = MemoryQuery.Request()
        req.query = query
        req.top_k = top_k

        future = self._query_client.call_async(req)
        rclpy.spin_until_future_complete(self._node, future, timeout_sec=5.0)

        res = future.result()
        if res is None:
            logger.error("memory/query call timed out")
            return []

        results = []
        for content, score, meta_json in zip(
            res.contents, res.scores, res.metadata_json
        ):
            meta = json.loads(meta_json) if meta_json else {}
            results.append(MemoryResult(content=content, score=score, metadata=meta))
        return results


# ---------------------------------------------------------------------------
# Direct in-process ChromaDB (fallback / testing)
# ---------------------------------------------------------------------------

class ChromaMemory(MemoryStore):
    """
    Direct in-process ChromaDB store.  Prefer running a standalone
    ChromaMemoryNode and using ServiceMemoryClient instead.
    """

    def __init__(
        self,
        persist_path: str = "/tmp/andr_memory",
        collection_name: str = "andr",
        embedding_model: str = "all-MiniLM-L6-v2",
    ):
        import chromadb
        from chromadb.utils import embedding_functions

        self._client = chromadb.PersistentClient(path=persist_path)
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model
        )
        self._col = self._client.get_or_create_collection(
            name=collection_name, embedding_function=ef
        )
        logger.info(
            "ChromaMemory: collection='%s' path='%s' model='%s' (%d docs)",
            collection_name, persist_path, embedding_model, self._col.count(),
        )

    def add(self, text: str, metadata: Optional[dict] = None) -> None:
        self._col.add(
            documents=[text],
            metadatas=[metadata or {}],
            ids=[str(uuid.uuid4())],
        )

    def query(self, query: str, top_k: int = 4) -> list[MemoryResult]:
        n = min(top_k, self._col.count())
        if n == 0:
            return []
        res = self._col.query(query_texts=[query], n_results=n)
        results = []
        for doc, dist, meta in zip(
            res["documents"][0], res["distances"][0], res["metadatas"][0]
        ):
            score = max(0.0, 1.0 - dist / 2.0)
            results.append(MemoryResult(content=doc, score=score, metadata=meta))
        return results


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_DIRECT_REGISTRY: dict[str, type[MemoryStore]] = {
    "chroma": ChromaMemory,
}


def create_memory(
    backend: str = "chroma",
    ros_node: Optional[Node] = None,
    use_service: bool = True,
    **kwargs,
) -> MemoryStore:
    """Instantiate a memory backend.

    Parameters
    ----------
    backend : str
        Backend name. Used for the direct (in-process) path only.
    ros_node : Node, optional
        ROS node for creating service clients.  Required when
        ``use_service=True``.
    use_service : bool
        If True (default), connect to a standalone memory node via
        ROS services.  If False, instantiate the backend in-process.
    """
    if use_service:
        if ros_node is None:
            raise ValueError("ros_node is required when use_service=True")
        return ServiceMemoryClient(ros_node, **kwargs)

    # Direct / in-process fallback
    backend = backend.lower()
    if backend not in _DIRECT_REGISTRY:
        raise ValueError(
            f"Unknown memory backend '{backend}'. "
            f"Available: {sorted(_DIRECT_REGISTRY)}"
        )
    return _DIRECT_REGISTRY[backend](**kwargs)
