"""
server.py — FastAPI web server for the ANDR web UI.

Endpoints
---------
  GET  /                  Serves index.html (dashboard)
  GET  /rviz              Serves rviz.html (2D map visualization)
  GET  /static/*          Static assets
  WS   /ws                WebSocket — bidirectional:
                            browser → server: {"type": "prompt", "text": "...", "context": "..."}
                                              {"type": "save_point", "map_name": "...", "label": "...", "x": ..., "y": ...}
                                              {"type": "get_points", "map_name": "..."}
                                              {"type": "get_maps"}
                            server → browser: event dicts from ROS topics

Run
---
  ros2 run andr_ui ui_server
"""

from __future__ import annotations

import asyncio
import importlib.resources
import json
import os
from typing import Set

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .ros_bridge import start_ros_thread

# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="ANDR UI")

# Global asyncio queue; events from ROS are put here by the bridge thread
_event_queue: asyncio.Queue[dict] = asyncio.Queue()

# Active WebSocket connections
_clients: Set[WebSocket] = set()

# ROS bridge (set during startup)
_bridge = None


# ── Startup / shutdown ───────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup() -> None:
    global _bridge

    # Capture the running event loop so the ROS thread can safely enqueue events
    loop = asyncio.get_running_loop()

    def _push(event: dict) -> None:
        """Called from the ROS spin thread — thread-safe put into the asyncio queue."""
        try:
            loop.call_soon_threadsafe(_event_queue.put_nowait, event)
        except RuntimeError:
            pass  # loop closed during shutdown

    _bridge = start_ros_thread(_push)

    # Background task: fan out queue events to all WebSocket clients
    asyncio.create_task(_broadcast_loop())


async def _broadcast_loop() -> None:
    """Pull events off the queue and push them to every connected browser."""
    while True:
        event = await _event_queue.get()
        payload = json.dumps(event)
        dead: list[WebSocket] = []
        for ws in list(_clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)


# ── Static files + HTML ──────────────────────────────────────────────────────

# Resolve the static directory:
#   1. Prefer the installed share path (colcon install)
#   2. Fall back to the in-source path (colcon build --symlink-install / dev)
_HERE = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR_DEV = os.path.join(_HERE, "static")

try:
    from ament_index_python.packages import get_package_share_directory
    _STATIC_DIR = os.path.join(get_package_share_directory("andr_ui"), "static")
    if not os.path.isdir(_STATIC_DIR):
        _STATIC_DIR = _STATIC_DIR_DEV
except Exception:
    _STATIC_DIR = _STATIC_DIR_DEV

app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    with open(os.path.join(_STATIC_DIR, "index.html"), "r") as f:
        return HTMLResponse(content=f.read())


@app.get("/rviz", response_class=HTMLResponse)
async def rviz() -> HTMLResponse:
    with open(os.path.join(_STATIC_DIR, "rviz.html"), "r") as f:
        return HTMLResponse(content=f.read())


# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)

    # Notify the new client that they connected
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
                text    = str(msg.get("text", "")).strip()
                context = str(msg.get("context", ""))
                if text and _bridge is not None:
                    _bridge.send_task(text, context)
                    # Echo back so the sender sees their own message in the log
                    await _broadcast({"type": "user_prompt", "text": text})

            elif msg_type == "save_point":
                if _bridge is not None:
                    _bridge.save_point(
                        map_name=str(msg.get("map_name", "")),
                        label=str(msg.get("label", "")),
                        x=float(msg.get("x", 0.0)),
                        y=float(msg.get("y", 0.0)),
                    )

            elif msg_type == "get_points":
                if _bridge is not None:
                    _bridge.get_points(
                        map_name=str(msg.get("map_name", "")),
                    )

            elif msg_type == "get_maps":
                if _bridge is not None:
                    _bridge.get_maps()

    except WebSocketDisconnect:
        _clients.discard(ws)


async def _broadcast(event: dict) -> None:
    payload = json.dumps(event)
    dead: list[WebSocket] = []
    for ws in list(_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    host = os.environ.get("ANDR_UI_HOST", "0.0.0.0")
    port = int(os.environ.get("ANDR_UI_PORT", "8080"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
