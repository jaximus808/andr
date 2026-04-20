"""memory_chroma_node.py — ChromaDB memory store node.

Standalone ROS 2 node that provides memory/add and memory/query services
backed by ChromaDB + sentence-transformers.  Replace this node with any
other BaseMemoryStore implementation to swap the memory backend.

ROS 2 parameters
----------------
persist_path        string   "/tmp/andr_memory"
collection_name     string   "andr"
embedding_model     string   "all-MiniLM-L6-v2"
"""

from __future__ import annotations

import logging
import uuid

import rclpy
from rclpy.executors import MultiThreadedExecutor

from andr_tools import BaseMemoryStore

logger = logging.getLogger(__name__)


class ChromaMemoryNode(BaseMemoryStore):
    """ChromaDB-backed memory store node."""

    STORE_NAME = "chroma"

    def __init__(self):
        super().__init__()

        self.declare_parameter("persist_path", "/tmp/andr_memory")
        self.declare_parameter("collection_name", "andr")
        self.declare_parameter("embedding_model", "all-MiniLM-L6-v2")

        persist_path = self.get_parameter("persist_path").get_parameter_value().string_value
        collection_name = self.get_parameter("collection_name").get_parameter_value().string_value
        embedding_model = self.get_parameter("embedding_model").get_parameter_value().string_value

        import chromadb
        from chromadb.utils import embedding_functions

        self._client = chromadb.PersistentClient(path=persist_path)
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=embedding_model
        )
        self._col = self._client.get_or_create_collection(
            name=collection_name, embedding_function=ef
        )
        self.get_logger().info(
            f"ChromaMemoryNode: collection='{collection_name}' "
            f"path='{persist_path}' model='{embedding_model}' "
            f"({self._col.count()} docs)"
        )

    def _add(self, text: str, metadata: dict) -> None:
        self._col.add(
            documents=[text],
            metadatas=[metadata or {}],
            ids=[str(uuid.uuid4())],
        )
        self.get_logger().debug("Added doc (total=%d)", self._col.count())

    def _query(self, query: str, top_k: int) -> list[tuple[str, float, dict]]:
        n = min(top_k, self._col.count())
        if n == 0:
            return []
        res = self._col.query(query_texts=[query], n_results=n)
        results = []
        for doc, dist, meta in zip(
            res["documents"][0], res["distances"][0], res["metadatas"][0]
        ):
            score = max(0.0, 1.0 - dist / 2.0)
            results.append((doc, score, meta))
        return results


def main(args=None):
    logging.basicConfig(level=logging.INFO)
    rclpy.init(args=args)
    node = ChromaMemoryNode()
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
