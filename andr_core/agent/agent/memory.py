"""memory.py — RAG memory store backends for ANDR."""

from __future__ import annotations

import abc
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

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


class ChromaMemory(MemoryStore):
    """
    Persistent vector store via ChromaDB + sentence-transformers.

    pip install chromadb sentence-transformers

    Parameters
    ----------
    persist_path:
        Directory for the Chroma database. Default: /tmp/andr_memory
    collection_name:
        Chroma collection name. Default: andr
    embedding_model:
        SentenceTransformer model name. Default: all-MiniLM-L6-v2
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
        logger.debug("ChromaMemory: added doc (total=%d)", self._col.count())

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


_REGISTRY: dict[str, type[MemoryStore]] = {
    "chroma": ChromaMemory,
}


def create_memory(backend: str = "chroma", **kwargs) -> MemoryStore:
    """Instantiate a memory backend by name. Passes kwargs to the constructor."""
    backend = backend.lower()
    if backend not in _REGISTRY:
        raise ValueError(f"Unknown memory backend '{backend}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[backend](**kwargs)
