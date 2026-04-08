"""world_resolver.py — Manage `~/andr_worlds/` for `andr start --sim`.

Mirrors the `~/andr_maps/` convention from `andr_nav.map_server`. The user
owns the directory; the pip package only materializes a default world on
first launch and never overwrites user edits.

Resolution order for the `--world` CLI argument:
  1. Absolute path                       → use as-is
  2. Relative path containing a slash    → resolve from cwd
  3. Bare name                           → look in ~/andr_worlds/
  4. None                                → read sim_config.json["active_world"],
                                            falling back to "default.world"
  5. default.world missing               → materialize from bundled asset
  6. Resolved path missing               → raise FileNotFoundError listing
                                            available worlds
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from typing import List, Optional

from andr.runtime.andr_sim import DEFAULT_WORLD as _BUNDLED_DEFAULT_WORLD


ANDR_WORLDS_DIR = os.path.expanduser("~/andr_worlds")
SIM_CONFIG_FILE = os.path.join(ANDR_WORLDS_DIR, "sim_config.json")
DEFAULT_WORLD_NAME = "default.world"


def ensure_worlds_dir() -> str:
    """Create ~/andr_worlds/ if it doesn't exist. Returns the path."""
    os.makedirs(ANDR_WORLDS_DIR, exist_ok=True)
    return ANDR_WORLDS_DIR


def materialize_default_world() -> str:
    """Copy the bundled default world to ~/andr_worlds/default.world.

    Idempotent — never overwrites an existing file. Same contract as
    `andr init` scaffold files: once it's on disk, it belongs to the user.

    Returns the path to the materialized default world.
    """
    ensure_worlds_dir()
    target = os.path.join(ANDR_WORLDS_DIR, DEFAULT_WORLD_NAME)
    if not os.path.exists(target):
        shutil.copy(_BUNDLED_DEFAULT_WORLD, target)
    return target


def list_available_worlds() -> List[str]:
    """Return sorted list of .world filenames in ~/andr_worlds/."""
    if not os.path.isdir(ANDR_WORLDS_DIR):
        return []
    return sorted(
        f for f in os.listdir(ANDR_WORLDS_DIR) if f.endswith(".world")
    )


def _read_sim_config() -> dict:
    if not os.path.isfile(SIM_CONFIG_FILE):
        return {}
    try:
        with open(SIM_CONFIG_FILE) as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def write_sim_config(active_world: str) -> None:
    """Record the currently active world in ~/andr_worlds/sim_config.json."""
    ensure_worlds_dir()
    payload = {
        "active_world": os.path.basename(active_world),
        "active_world_path": os.path.abspath(active_world),
        "last_used": datetime.now(timezone.utc).isoformat(),
    }
    with open(SIM_CONFIG_FILE, "w") as f:
        json.dump(payload, f, indent=2)


def resolve_world(arg: Optional[str]) -> str:
    """Resolve a `--world` argument to an absolute path on disk.

    Always materializes the default world if it's needed and missing.
    Raises FileNotFoundError with an actionable message if the resolved
    path doesn't exist.
    """
    ensure_worlds_dir()

    # Always make sure default.world exists — it's the fallback for
    # both `--world` resolution and the bundled launch file's default arg.
    materialize_default_world()

    if arg:
        candidate = _resolve_arg(arg)
    else:
        # No --world: check sim_config, fall back to default.world
        cfg = _read_sim_config()
        active = cfg.get("active_world") or DEFAULT_WORLD_NAME
        candidate = os.path.join(ANDR_WORLDS_DIR, active)

    if not os.path.isfile(candidate):
        available = list_available_worlds()
        avail_str = (
            "\n  ".join(available) if available else "(none)"
        )
        raise FileNotFoundError(
            f"World file not found: {candidate}\n\n"
            f"Available worlds in {ANDR_WORLDS_DIR}:\n  {avail_str}"
        )

    return os.path.abspath(candidate)


def _resolve_arg(arg: str) -> str:
    """Apply resolution rules 1-3 to a `--world` argument."""
    if os.path.isabs(arg):
        return arg
    if os.sep in arg or (os.altsep and os.altsep in arg):
        return os.path.abspath(arg)
    # Bare name → look in ~/andr_worlds/
    return os.path.join(ANDR_WORLDS_DIR, arg)
