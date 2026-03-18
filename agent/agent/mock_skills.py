"""mock_skills.py — Per-skill mock handlers used when the real skill_executor is offline.

Each handler receives the validated args dict and returns a JSON string that
mirrors what the real skill_executor node would produce.  The mock data is
plausible enough for agent loop testing without any hardware.

Handler registration
--------------------
Add an entry to _HANDLERS keyed by skill name.  The function signature is:

    def _handle_<skill>(args: dict) -> dict:
        ...
        return {...}   # will be JSON-encoded by dispatch()
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# see — camera / object detection mock
# ---------------------------------------------------------------------------

# Pool of plausible detections the mock detector can "see"
_DETECTION_POOL = [
    {"label": "person",      "confidence": 0.92, "distance_m": 1.8},
    {"label": "chair",       "confidence": 0.87, "distance_m": 1.2},
    {"label": "mug",         "confidence": 0.81, "distance_m": 0.6},
    {"label": "table",       "confidence": 0.95, "distance_m": 1.5},
    {"label": "laptop",      "confidence": 0.78, "distance_m": 0.9},
    {"label": "door",        "confidence": 0.91, "distance_m": 3.0},
    {"label": "plant",       "confidence": 0.74, "distance_m": 2.1},
    {"label": "backpack",    "confidence": 0.69, "distance_m": 0.8},
    {"label": "book",        "confidence": 0.83, "distance_m": 0.5},
    {"label": "water_bottle","confidence": 0.77, "distance_m": 0.4},
]


def _handle_see(args: dict) -> dict:
    pan_deg  = float(args.get("pan_deg",  0))
    tilt_deg = float(args.get("tilt_deg", 0))
    fov_deg  = float(args.get("fov_deg",  60))

    # Simulate shutter delay
    time.sleep(0.3)

    # Pick 2–5 random detections; bias count by FOV width
    max_det = max(2, int(fov_deg / 15))
    detections = random.sample(_DETECTION_POOL, k=min(max_det, len(_DETECTION_POOL)))

    # Add bounding-box placeholders (normalised 0–1)
    for d in detections:
        cx = random.uniform(0.1, 0.9)
        cy = random.uniform(0.1, 0.9)
        w  = random.uniform(0.05, 0.3)
        h  = random.uniform(0.05, 0.3)
        d["bbox_xywh_norm"] = [round(cx, 3), round(cy, 3), round(w, 3), round(h, 3)]

    return {
        "success":    True,
        "pan_deg":    pan_deg,
        "tilt_deg":   tilt_deg,
        "fov_deg":    fov_deg,
        "detections": detections,
        "note":       "[MOCK] Object detector offline — synthetic detections returned.",
    }


# ---------------------------------------------------------------------------
# move_to_landmark — Nav2 / SLAM mock
# ---------------------------------------------------------------------------

# Simulated travel times per landmark (seconds) and known positions
_LANDMARK_DB: dict[str, dict] = {
    "kitchen":       {"x": 3.2,  "y": -1.0, "travel_s": 8},
    "living_room":   {"x": 0.5,  "y":  0.8, "travel_s": 4},
    "charging_dock": {"x": -2.0, "y":  0.2, "travel_s": 6},
    "front_door":    {"x": 4.5,  "y":  1.5, "travel_s": 12},
    "bedroom":       {"x": -3.1, "y":  2.0, "travel_s": 10},
    "hallway":       {"x": 1.0,  "y": -0.5, "travel_s": 3},
    "bathroom":      {"x": -1.5, "y":  2.8, "travel_s": 7},
}


def _handle_move_to_landmark(args: dict) -> dict:
    landmark = args.get("landmark", "").lower().replace(" ", "_")
    approach = float(args.get("approach_distance_m", 0.3))

    if landmark not in _LANDMARK_DB:
        known = sorted(_LANDMARK_DB)
        return {
            "success":       False,
            "landmark":      landmark,
            "error_message": f"Unknown landmark '{landmark}'. Known: {known}",
        }

    info = _LANDMARK_DB[landmark]
    # Simulate travel with a small chance of obstacle
    time.sleep(min(info["travel_s"] * 0.05, 1.0))  # compressed for testing
    obstacle_hit = random.random() < 0.08  # 8% chance

    if obstacle_hit:
        return {
            "success":       False,
            "landmark":      landmark,
            "error_message": "Navigation aborted — unexpected obstacle detected at mid-path.",
            "partial_progress": round(random.uniform(0.3, 0.8), 2),
        }

    return {
        "success":            True,
        "landmark":           landmark,
        "final_position":     {"x": info["x"], "y": info["y"]},
        "approach_distance_m": approach,
        "nav_status":         "ARRIVED",
        "note":               "[MOCK] Nav2 offline — simulated arrival.",
    }


# ---------------------------------------------------------------------------
# move_sequence — directional primitives mock
# ---------------------------------------------------------------------------

_VALID_STEP_TYPES = {"forward", "backward", "turn_left", "turn_right", "pause"}


def _handle_move_sequence(args: dict) -> dict:
    steps = args.get("steps", [])
    if not steps:
        return {"success": False, "error_message": "No steps provided."}

    executed = []
    x, y, heading_deg = 0.0, 0.0, 0.0  # dead-reckoning from current pose

    for i, step in enumerate(steps):
        stype = step.get("type", "")
        value = float(step.get("value", 0))

        if stype not in _VALID_STEP_TYPES:
            return {
                "success":       False,
                "error_message": f"Step {i}: unknown type '{stype}'. "
                                 f"Valid: {sorted(_VALID_STEP_TYPES)}",
                "executed_steps": executed,
            }

        # Simulate slip / timing jitter
        actual = round(value * random.uniform(0.95, 1.05), 3)
        time.sleep(min(value * 0.02, 0.2))

        if stype == "forward":
            x += actual * math.cos(math.radians(heading_deg))
            y += actual * math.sin(math.radians(heading_deg))
        elif stype == "backward":
            x -= actual * math.cos(math.radians(heading_deg))
            y -= actual * math.sin(math.radians(heading_deg))
        elif stype == "turn_left":
            heading_deg = (heading_deg + actual) % 360
        elif stype == "turn_right":
            heading_deg = (heading_deg - actual) % 360
        # pause: no position change

        executed.append({"type": stype, "commanded": value, "actual": actual})

    return {
        "success":        True,
        "steps_executed": len(executed),
        "steps":          executed,
        "dead_reckoning": {
            "dx_m": round(x, 3),
            "dy_m": round(y, 3),
            "heading_deg": round(heading_deg, 1),
        },
        "note": "[MOCK] Motor controllers offline — dead-reckoning only.",
    }


# ---------------------------------------------------------------------------
# speak — TTS mock
# ---------------------------------------------------------------------------

def _handle_speak(args: dict) -> dict:
    text  = args.get("text", "")
    voice = args.get("voice", "default")

    if not text:
        return {"success": False, "error_message": "No text provided."}

    # Rough estimate: ~150 wpm → ~2.5 chars/sec
    duration_s = max(0.5, len(text) / 12.0)
    time.sleep(min(duration_s * 0.05, 0.5))  # compressed

    return {
        "success":        True,
        "text":           text,
        "voice":          voice,
        "duration_s":     round(duration_s, 2),
        "characters":     len(text),
        "note":           "[MOCK] TTS engine offline — playback simulated.",
    }


# ---------------------------------------------------------------------------
# animate — body animation mock
# ---------------------------------------------------------------------------

_KNOWN_ANIMATIONS = {
    "nod":           {"duration_s": 1.2},
    "wave":          {"duration_s": 2.5},
    "look_around":   {"duration_s": 4.0},
    "idle":          {"duration_s": 3.0},
    "celebrate":     {"duration_s": 3.5},
    "confused":      {"duration_s": 2.0},
    "follow_me":     {"duration_s": 2.0},
    "point_forward": {"duration_s": 1.8},
}


def _handle_animate(args: dict) -> dict:
    name = args.get("animation_name", "").lower().replace(" ", "_")
    loop = bool(args.get("loop", False))

    if name not in _KNOWN_ANIMATIONS:
        known = sorted(_KNOWN_ANIMATIONS)
        return {
            "success":       False,
            "animation_name": name,
            "error_message": f"Unknown animation '{name}'. Known: {known}",
        }

    clip = _KNOWN_ANIMATIONS[name]
    duration = clip["duration_s"] if not loop else 0.0  # looping runs until stopped
    time.sleep(min(duration * 0.05, 0.3))

    return {
        "success":        True,
        "animation_name": name,
        "loop":           loop,
        "duration_s":     duration,
        "status":         "PLAYING" if loop else "FINISHED",
        "note":           "[MOCK] Animation controller offline — playback simulated.",
    }


# ---------------------------------------------------------------------------
# navigate_to_point — Nav2 point navigation mock
# ---------------------------------------------------------------------------

def _handle_navigate_to_point(args: dict) -> dict:
    point_name = args.get("point_name", "").strip()
    map_name = args.get("map_name", "").strip()

    if not point_name:
        return {"success": False, "error_message": "point_name is required."}
    if not map_name:
        return {"success": False, "error_message": "map_name is required."}

    # Simulate map service lookup + short travel delay
    time.sleep(0.2)

    # Small chance of failure (point not found)
    if random.random() < 0.05:
        return {
            "success": False,
            "point_name": point_name,
            "map_name": map_name,
            "error_message": (
                f"[MOCK] Point '{point_name}' not found on map '{map_name}'."
            ),
        }

    # Simulate nav2 travel with random distance
    travel_s = random.uniform(3, 10)
    time.sleep(min(travel_s * 0.05, 0.5))  # compressed

    x = round(random.uniform(-5.0, 5.0), 2)
    y = round(random.uniform(-5.0, 5.0), 2)

    return {
        "success": True,
        "point_name": point_name,
        "map_name": map_name,
        "final_position": {"x": x, "y": y},
        "nav_status": "ARRIVED",
        "note": "[MOCK] Nav2 offline — simulated arrival at named point.",
    }


# ---------------------------------------------------------------------------
# spin — rotate in place mock
# ---------------------------------------------------------------------------

def _handle_spin(args: dict) -> dict:
    duration_s = float(args.get("duration_s", 3.0))
    speed_deg_s = float(args.get("speed_deg_s", 90.0))
    direction = args.get("direction", "left")

    if direction not in ("left", "right"):
        return {
            "success": False,
            "error_message": f"Invalid direction '{direction}'. Must be 'left' or 'right'.",
        }

    total_deg = speed_deg_s * duration_s
    time.sleep(min(duration_s * 0.05, 0.5))  # compressed

    return {
        "success": True,
        "direction": direction,
        "duration_s": duration_s,
        "total_rotation_deg": total_deg,
        "note": "[MOCK] cmd_vel offline — spin simulated.",
    }


# ---------------------------------------------------------------------------
# Dispatch table + public entry point
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Any] = {
    "see":                 _handle_see,
    "move_to_landmark":    _handle_move_to_landmark,
    "move_sequence":       _handle_move_sequence,
    "speak":               _handle_speak,
    "animate":             _handle_animate,
    "spin":                _handle_spin,
    "navigate_to_point":   _handle_navigate_to_point,
}


def dispatch(skill_name: str, args: dict) -> str:
    """
    Route skill_name to its mock handler and return a JSON result string.
    Falls back to a generic stub for unregistered skills.
    """
    handler = _HANDLERS.get(skill_name)
    if handler is None:
        logger.debug("mock_skills: no handler for '%s' — generic stub.", skill_name)
        result = {
            "success": True,
            "mock":    True,
            "skill":   skill_name,
            "args":    args,
            "note":    f"[MOCK] No specific handler for '{skill_name}'.",
        }
    else:
        try:
            result = handler(args)
            result.setdefault("mock", True)
        except Exception as exc:  # noqa: BLE001
            logger.error("mock_skills: handler for '%s' raised %s", skill_name, exc)
            result = {"success": False, "mock": True, "skill": skill_name,
                      "error_message": str(exc)}

    payload = json.dumps(result)
    logger.debug("mock_skills.dispatch('%s') → %s", skill_name, payload[:160])
    return payload
