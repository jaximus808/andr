"""memory_manager.py — ROS 2 node that manages multi-store RAG memory.

The MemoryManagerNode owns all configured memory stores and exposes them
via ROS 2 services.  The agent (and any other node) interacts with memory
exclusively through these services.

ROS 2 parameters
----------------
stores_json       string   '{}'       JSON-serialised stores config
default_store     string   'default'  Name of the default store
default_top_k     int      4          Default top_k for queries

Services
--------
memory_manager/store        StoreMemory       — add an entry to a store
memory_manager/query        QueryMemory       — retrieve entries (fan-out)
memory_manager/list_stores  ListMemoryStores  — list all configured stores
memory_manager/status       GetMemoryStatus   — detailed status of one store
"""

from __future__ import annotations

import json
import logging
import os

import rclpy
from rclpy.node import Node

from andr_msgs.srv import (
    StoreMemory,
    QueryMemory,
    ListMemoryStores,
    GetMemoryStatus,
)

from .memory import ChromaMemory, MemoryFullError, MemoryStore, create_memory

logger = logging.getLogger(__name__)

# Default stores config when none is provided
_DEFAULT_STORES = {
    "default": {
        "backend": "chroma",
        "path": "~/.andr/memory/default",
        "max_size_mb": 512,
        "embedding_model": "all-MiniLM-L6-v2",
        "on_full": "warn",
    }
}


class MemoryManagerNode(Node):
    """ROS 2 node that manages multiple named memory stores."""

    def __init__(self):
        super().__init__("memory_manager")

        # -- Declare parameters ------------------------------------------------
        self.declare_parameter("stores_json", json.dumps(_DEFAULT_STORES))
        self.declare_parameter("default_store", "default")
        self.declare_parameter("default_top_k", 4)

        # -- Parse config & create stores --------------------------------------
        stores_json = (
            self.get_parameter("stores_json")
            .get_parameter_value()
            .string_value
        )
        self._default_store = (
            self.get_parameter("default_store")
            .get_parameter_value()
            .string_value
        )
        self._default_top_k = (
            self.get_parameter("default_top_k")
            .get_parameter_value()
            .integer_value
        )

        stores_cfg = json.loads(stores_json) if stores_json else _DEFAULT_STORES
        self._stores: dict[str, MemoryStore] = {}
        self._stores_cfg: dict[str, dict] = stores_cfg

        for name, cfg in stores_cfg.items():
            self._stores[name] = self._create_store(name, cfg)

        self.get_logger().info(
            f"MemoryManager: {len(self._stores)} store(s) configured: "
            f"{list(self._stores.keys())} (default='{self._default_store}')"
        )

        # -- Create services ---------------------------------------------------
        self._store_srv = self.create_service(
            StoreMemory, "memory_manager/store", self._handle_store
        )
        self._query_srv = self.create_service(
            QueryMemory, "memory_manager/query", self._handle_query
        )
        self._list_srv = self.create_service(
            ListMemoryStores, "memory_manager/list_stores", self._handle_list
        )
        self._status_srv = self.create_service(
            GetMemoryStatus, "memory_manager/status", self._handle_status
        )

        self.get_logger().info("MemoryManager services ready.")

    # ------------------------------------------------------------------
    # Store creation
    # ------------------------------------------------------------------

    def _create_store(self, name: str, cfg: dict) -> MemoryStore:
        """Instantiate a MemoryStore from a config dict."""
        backend = cfg.get("backend", "chroma")
        path = os.path.expanduser(cfg.get("path", f"~/.andr/memory/{name}"))
        max_size_mb = cfg.get("max_size_mb", 0)
        max_size_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb else 0
        embedding_model = cfg.get("embedding_model", "all-MiniLM-L6-v2")
        on_full = cfg.get("on_full", "warn")

        os.makedirs(path, exist_ok=True)

        store = create_memory(
            backend=backend,
            persist_path=path,
            collection_name=name,
            embedding_model=embedding_model,
            max_size_bytes=max_size_bytes,
            on_full=on_full,
        )
        self.get_logger().info(
            f"  Store '{name}': backend={backend} path={path} "
            f"max_size={max_size_mb}MB on_full={on_full}"
        )
        return store

    # ------------------------------------------------------------------
    # Service: store
    # ------------------------------------------------------------------

    def _handle_store(self, req: StoreMemory.Request, res: StoreMemory.Response):
        store_name = req.store_name or self._default_store
        store = self._stores.get(store_name)

        if store is None:
            res.success = False
            res.id = ""
            res.message = (
                f"Unknown store '{store_name}'. "
                f"Available: {list(self._stores.keys())}"
            )
            return res

        metadata = {}
        if req.metadata_json:
            try:
                metadata = json.loads(req.metadata_json)
            except json.JSONDecodeError as e:
                res.success = False
                res.id = ""
                res.message = f"Invalid metadata_json: {e}"
                return res

        try:
            doc_id = store.add(req.text, metadata=metadata)
            res.success = True
            res.id = doc_id
            res.message = f"Stored in '{store_name}' (id={doc_id})"
            self.get_logger().info(
                f"Stored entry in '{store_name}': {req.text[:80]}..."
            )
        except MemoryFullError as e:
            res.success = False
            res.id = ""
            res.message = str(e)
        except Exception as e:
            res.success = False
            res.id = ""
            res.message = f"Store error: {e}"
            self.get_logger().error(f"Store error in '{store_name}': {e}")

        return res

    # ------------------------------------------------------------------
    # Service: query (fan-out across all stores when store_name is empty)
    # ------------------------------------------------------------------

    def _handle_query(self, req: QueryMemory.Request, res: QueryMemory.Response):
        top_k = req.top_k if req.top_k > 0 else self._default_top_k
        store_name = req.store_name

        try:
            if store_name:
                # Query a specific store
                store = self._stores.get(store_name)
                if store is None:
                    res.success = False
                    res.results_json = "[]"
                    res.message = (
                        f"Unknown store '{store_name}'. "
                        f"Available: {list(self._stores.keys())}"
                    )
                    return res
                results = store.query(req.query, top_k=top_k)
            else:
                # Fan-out: query ALL stores, merge results by score
                results = self._fan_out_query(req.query, top_k)

            results_dicts = [r.to_dict() for r in results]
            res.success = True
            res.results_json = json.dumps(results_dicts)
            res.message = f"{len(results)} result(s)"

        except Exception as e:
            res.success = False
            res.results_json = "[]"
            res.message = f"Query error: {e}"
            self.get_logger().error(f"Query error: {e}")

        return res

    def _fan_out_query(self, query: str, top_k: int) -> list:
        """Query all stores and merge results sorted by score."""
        from .memory import MemoryResult

        all_results: list[MemoryResult] = []
        for name, store in self._stores.items():
            try:
                results = store.query(query, top_k=top_k)
                # Tag each result with its source store
                for r in results:
                    r.metadata["_store"] = name
                all_results.extend(results)
            except Exception as e:
                self.get_logger().warning(
                    f"Fan-out query failed for store '{name}': {e}"
                )

        # Sort by score descending, return top_k
        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:top_k]

    # ------------------------------------------------------------------
    # Service: list_stores
    # ------------------------------------------------------------------

    def _handle_list(self, _req, res: ListMemoryStores.Response):
        for name, store in self._stores.items():
            cfg = self._stores_cfg.get(name, {})
            max_size_mb = cfg.get("max_size_mb", 0)
            max_size_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb else 0

            res.store_names.append(name)
            res.backends.append(cfg.get("backend", "chroma"))
            res.paths.append(store.persist_path)
            res.doc_counts.append(store.count())
            res.size_bytes.append(store.disk_usage_bytes())
            res.max_size_bytes.append(max_size_bytes)

        return res

    # ------------------------------------------------------------------
    # Service: status
    # ------------------------------------------------------------------

    def _handle_status(self, req: GetMemoryStatus.Request, res: GetMemoryStatus.Response):
        store_name = req.store_name or self._default_store
        store = self._stores.get(store_name)

        if store is None:
            res.success = False
            res.message = (
                f"Unknown store '{store_name}'. "
                f"Available: {list(self._stores.keys())}"
            )
            return res

        cfg = self._stores_cfg.get(store_name, {})
        max_size_mb = cfg.get("max_size_mb", 0)
        max_size_bytes = int(max_size_mb * 1024 * 1024) if max_size_mb else 0

        res.success = True
        res.store_name = store_name
        res.backend = cfg.get("backend", "chroma")
        res.path = store.persist_path
        res.doc_count = store.count()
        res.size_bytes = store.disk_usage_bytes()
        res.max_size_bytes = max_size_bytes
        res.embedding_model = getattr(store, "embedding_model", "unknown")
        res.on_full = getattr(store, "on_full", "warn")
        res.message = "ok"

        return res


def main(args=None):
    logging.basicConfig(level=logging.INFO)
    rclpy.init(args=args)
    node = MemoryManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
