"""memory.py — RAG memory store backends for ANDR.

Provides an abstract MemoryStore interface and a ChromaDB implementation
with configurable size limits and eviction policies.
"""

from __future__ import annotations

import abc
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MemoryResult:
    content: str
    score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_prompt_line(self) -> str:
        source = self.metadata.get("source", "unknown")
        filled = round(self.score * 5)
        bar = "\u2588" * filled + "\u2591" * (5 - filled)
        return f"  [{bar}] ({source}): {self.content}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "score": self.score,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class MemoryStore(abc.ABC):
    """Abstract RAG store — all backends implement this interface."""

    @abc.abstractmethod
    def add(self, text: str, metadata: Optional[dict] = None) -> str:
        """Store text with optional metadata. Returns the document id."""
        ...

    @abc.abstractmethod
    def query(self, query: str, top_k: int = 4) -> list[MemoryResult]:
        """Retrieve top_k relevant results for a query."""
        ...

    @abc.abstractmethod
    def count(self) -> int:
        """Return the number of stored documents."""
        ...

    @abc.abstractmethod
    def disk_usage_bytes(self) -> int:
        """Return approximate bytes used on disk."""
        ...

    @property
    @abc.abstractmethod
    def persist_path(self) -> str:
        """Return the path where this store persists data."""
        ...

    def to_prompt_block(self, results: list[MemoryResult]) -> str:
        if not results:
            return "MEMORY (RAG)\n============\n(no relevant entries found)"
        lines = ["MEMORY (RAG)", "============"]
        for r in results:
            lines.append(r.to_prompt_line())
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Eviction policies
# ---------------------------------------------------------------------------

VALID_ON_FULL = ("reject", "evict", "warn")


# ---------------------------------------------------------------------------
# ChromaDB implementation
# ---------------------------------------------------------------------------

class ChromaMemory(MemoryStore):
    """
    Persistent vector store via ChromaDB + sentence-transformers.

    Parameters
    ----------
    persist_path:
        Directory for the Chroma database.  Default: /tmp/andr_memory
    collection_name:
        Chroma collection name.  Default: andr
    embedding_model:
        SentenceTransformer model name.  Default: all-MiniLM-L6-v2
    max_size_bytes:
        Maximum disk usage in bytes.  0 = unlimited.
    on_full:
        Policy when max_size_bytes is exceeded:
        - "reject"  — refuse new entries (return error)
        - "evict"   — delete oldest entries until under limit
        - "warn"    — log a warning but allow the insert
    """

    def __init__(
        self,
        persist_path: str = "/tmp/andr_memory",
        collection_name: str = "andr",
        embedding_model: str = "all-MiniLM-L6-v2",
        max_size_bytes: int = 0,
        on_full: str = "warn",
    ):
        if on_full not in VALID_ON_FULL:
            raise ValueError(
                f"Invalid on_full policy '{on_full}'. Must be one of {VALID_ON_FULL}"
            )

        import chromadb
        from chromadb.utils import embedding_functions

        self._persist_path = os.path.expanduser(persist_path)
        os.makedirs(self._persist_path, exist_ok=True)

        self._max_size_bytes = max_size_bytes
        self._on_full = on_full
        self._embedding_model = embedding_model

        self._client = chromadb.PersistentClient(path=self._persist_path)
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model
        )
        self._col = self._client.get_or_create_collection(
            name=collection_name, embedding_function=ef
        )
        logger.info(
            "ChromaMemory: collection='%s' path='%s' model='%s' (%d docs) "
            "max_size=%s on_full='%s'",
            collection_name, self._persist_path, embedding_model,
            self._col.count(),
            f"{max_size_bytes / (1024 * 1024):.0f}MB" if max_size_bytes else "unlimited",
            on_full,
        )

    # -- Public interface ---------------------------------------------------

    def add(self, text: str, metadata: Optional[dict] = None) -> str:
        doc_id = str(uuid.uuid4())

        # Check size limit before adding
        if self._max_size_bytes > 0:
            usage = self.disk_usage_bytes()
            if usage >= self._max_size_bytes:
                if self._on_full == "reject":
                    msg = (
                        f"Memory store at {self._persist_path} is full "
                        f"({usage} bytes >= {self._max_size_bytes} byte limit). "
                        f"Cannot add new entry."
                    )
                    logger.warning(msg)
                    raise MemoryFullError(msg)

                elif self._on_full == "evict":
                    self._evict_oldest()

                elif self._on_full == "warn":
                    logger.warning(
                        "Memory store at %s exceeds size limit "
                        "(%d bytes >= %d byte limit). Adding anyway (on_full=warn).",
                        self._persist_path, usage, self._max_size_bytes,
                    )

        self._col.add(
            documents=[text],
            metadatas=[metadata or {}],
            ids=[doc_id],
        )
        logger.debug("ChromaMemory: added doc id=%s (total=%d)", doc_id, self._col.count())
        return doc_id

    def query(self, query: str, top_k: int = 4) -> list[MemoryResult]:
        n = min(top_k, self._col.count())
        if n == 0:
            return []
        res = self._col.query(query_texts=[query], n_results=n)
        results = []
        for doc, dist, meta in zip(
            res["documents"][0], res["distances"][0], res["metadatas"][0]
        ):
            # Chroma returns L2 distances; convert to a 0-1 score
            score = max(0.0, 1.0 - dist / 2.0)
            results.append(MemoryResult(content=doc, score=score, metadata=meta))
        logger.debug("ChromaMemory.query('%s') -> %d results", query[:60], len(results))
        return results

    def count(self) -> int:
        return self._col.count()

    def disk_usage_bytes(self) -> int:
        total = 0
        for dirpath, _dirnames, filenames in os.walk(self._persist_path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        return total

    @property
    def persist_path(self) -> str:
        return self._persist_path

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    @property
    def on_full(self) -> str:
        return self._on_full

    # -- Eviction -----------------------------------------------------------

    def _evict_oldest(self, batch_size: int = 10) -> None:
        """Delete the oldest entries to free space.

        ChromaDB doesn't track insertion order natively, so we use the
        document IDs (UUIDs) as a proxy — we simply delete the first N
        documents returned by peek().
        """
        total = self._col.count()
        if total == 0:
            return

        to_delete = min(batch_size, total)
        peek = self._col.peek(limit=to_delete)
        ids_to_delete = peek["ids"]

        if ids_to_delete:
            self._col.delete(ids=ids_to_delete)
            logger.info(
                "ChromaMemory: evicted %d oldest entries (was %d, now %d)",
                len(ids_to_delete), total, self._col.count(),
            )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MemoryFullError(Exception):
    """Raised when a store is full and on_full='reject'."""


# ---------------------------------------------------------------------------
# Registry & factory
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[MemoryStore]] = {
    "chroma": ChromaMemory,
}


def create_memory(backend: str = "chroma", **kwargs) -> MemoryStore:
    """Instantiate a memory backend by name. Passes kwargs to the constructor."""
    backend = backend.lower()
    if backend not in _REGISTRY:
        raise ValueError(f"Unknown memory backend '{backend}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[backend](**kwargs)
