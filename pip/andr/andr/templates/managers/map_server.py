"""Map management service node.

Provides services to save the current SLAM map and list saved maps.
Saved maps include both the occupancy grid (.pgm/.yaml) and the
SLAM Toolbox pose graph (.posegraph/.data) for later localization.

Points of interest are stored in a SQLite database (maps.db) inside
the maps directory, with a foreign-key join from points -> maps.
"""

import json
import os
import pathlib
import sqlite3
import subprocess
import time

import yaml
import numpy as np
import rclpy
from rclpy.node import Node
from nav_msgs.msg import OccupancyGrid

from andr_msgs.srv import (
    SaveMap, GetMaps, SavePoint, GetMapPoints, GetMapWithPoints,
    SetSlamConfig, GetSlamConfig, RestartSlam, GetPointCoordinates,
)

# Optional imports for simulation / SLAM / Nav2
try:
    from slam_toolbox.srv import SerializePoseGraph
    _HAS_SLAM_TOOLBOX = True
except ImportError:
    _HAS_SLAM_TOOLBOX = False

try:
    from gazebo_msgs.srv import SetEntityState
    _HAS_GAZEBO = True
except ImportError:
    _HAS_GAZEBO = False

try:
    from nav2_msgs.srv import ManageLifecycleNodes
    _HAS_NAV2 = True
except ImportError:
    _HAS_NAV2 = False


_MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"
DEFAULT_MAPS_DIR = os.path.expanduser("~/andr_maps")


class MapServer(Node):
    def __init__(self):
        super().__init__("map_server")

        self.declare_parameter("maps_dir", DEFAULT_MAPS_DIR)
        self._maps_dir = self.get_parameter("maps_dir").get_parameter_value().string_value

        pathlib.Path(self._maps_dir).mkdir(parents=True, exist_ok=True)

        # SQLite database
        self._db_path = os.path.join(self._maps_dir, "maps.db")
        self._db = sqlite3.connect(self._db_path, check_same_thread=False)
        self._init_db()

        # Cache latest occupancy grid
        self._latest_map: OccupancyGrid | None = None
        self._map_sub = self.create_subscription(
            OccupancyGrid, "/map", self._map_cb, 10
        )

        # Optional: SLAM Toolbox serialization client
        self._serialize_client = None
        if _HAS_SLAM_TOOLBOX:
            self._serialize_client = self.create_client(
                SerializePoseGraph, "/slam_toolbox/serialize_map"
            )

        # Optional: Gazebo entity state client (robot pose reset)
        self._set_entity_client = None
        if _HAS_GAZEBO:
            self._set_entity_client = self.create_client(
                SetEntityState, "/set_entity_state"
            )

        # Optional: Nav2 lifecycle manager client
        self._nav2_lifecycle_client = None
        if _HAS_NAV2:
            self._nav2_lifecycle_client = self.create_client(
                ManageLifecycleNodes, "/lifecycle_manager_navigation/manage_nodes"
            )

        # Services
        self.create_service(SaveMap, "map_manager/save_map", self._save_map_cb)
        self.create_service(GetMaps, "map_manager/get_maps", self._get_maps_cb)
        self.create_service(SavePoint, "map_manager/save_point", self._save_point_cb)
        self.create_service(GetMapPoints, "map_manager/get_map_points", self._get_map_points_cb)
        self.create_service(GetMapWithPoints, "map_manager/get_map_with_points", self._get_map_with_points_cb)
        self.create_service(SetSlamConfig, "map_manager/set_slam_config", self._set_slam_config_cb)
        self.create_service(GetSlamConfig, "map_manager/get_slam_config", self._get_slam_config_cb)
        self.create_service(RestartSlam, "map_manager/restart_slam", self._restart_slam_cb)
        self.create_service(GetPointCoordinates, "map_manager/get_point_coordinates", self._get_point_coordinates_cb)

        # SLAM params (only relevant when slam_toolbox is available)
        self.declare_parameter("slam_params_mapping", "")
        self.declare_parameter("slam_params_localization", "")

        self.get_logger().info(f"MapServer ready — maps stored in '{self._maps_dir}'")

    # ------------------------------------------------------------------
    # Database migrations
    # ------------------------------------------------------------------
    def _init_db(self):
        """Run any pending SQL migrations in version order."""
        bootstrap = _MIGRATIONS_DIR / "001_create_schema_migrations.sql"
        if bootstrap.exists():
            self._db.executescript(bootstrap.read_text())
            self._db.commit()

        applied = {
            row[0]
            for row in self._db.execute("SELECT version FROM schema_migrations")
        }

        pending = sorted(
            f for f in _MIGRATIONS_DIR.glob("*.sql") if f.name not in applied
        )
        for migration_file in pending:
            self.get_logger().info(f"Applying migration: {migration_file.name}")
            self._db.executescript(migration_file.read_text())
            self._db.execute(
                "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
                (migration_file.name,),
            )
            self._db.commit()

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------
    def _map_cb(self, msg: OccupancyGrid):
        self._latest_map = msg

    # ------------------------------------------------------------------
    # save_map service
    # ------------------------------------------------------------------
    def _save_map_cb(self, request, response):
        name = request.map_name.strip()
        if not name:
            response.success = False
            response.message = "map_name must not be empty"
            return response

        map_path = os.path.join(self._maps_dir, name)

        if self._latest_map is None:
            response.success = False
            response.message = "No map received on /map yet"
            return response

        try:
            self._save_occupancy_grid(map_path, self._latest_map)
        except Exception as e:
            response.success = False
            response.message = f"Failed to save occupancy grid: {e}"
            return response

        # Upsert map row in database
        grid = self._latest_map
        resolution = grid.info.resolution
        origin_x = grid.info.origin.position.x
        origin_y = grid.info.origin.position.y
        self._db.execute(
            """
            INSERT INTO maps (name, resolution, origin_x, origin_y)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                resolution = excluded.resolution,
                origin_x   = excluded.origin_x,
                origin_y   = excluded.origin_y
            """,
            (name, resolution, origin_x, origin_y),
        )
        self._db.commit()

        # Serialize SLAM Toolbox pose graph (if available)
        if self._serialize_client is not None:
            if self._serialize_client.wait_for_service(timeout_sec=2.0):
                req = SerializePoseGraph.Request()
                req.filename = map_path
                future = self._serialize_client.call_async(req)
                rclpy.spin_until_future_complete(self, future, timeout_sec=10.0)
                if future.result() is None:
                    self.get_logger().warn("slam_toolbox serialize call failed")
            else:
                self.get_logger().warn(
                    "slam_toolbox serialize service not available — saved occupancy grid only"
                )

        response.success = True
        response.message = f"Map '{name}' saved to {map_path}"
        self.get_logger().info(response.message)
        return response

    # ------------------------------------------------------------------
    # get_maps service
    # ------------------------------------------------------------------
    def _get_maps_cb(self, request, response):
        maps_dir = pathlib.Path(self._maps_dir)
        seen = set()
        for f in sorted(maps_dir.iterdir()):
            if f.suffix == ".yaml" and f.stem not in seen:
                seen.add(f.stem)
        response.map_names = sorted(seen)
        return response

    # ------------------------------------------------------------------
    # save_point service
    # ------------------------------------------------------------------
    def _save_point_cb(self, request, response):
        map_name = request.map_name.strip()
        label = request.label.strip()

        if not map_name:
            response.success = False
            response.message = "map_name must not be empty"
            return response
        if not label:
            response.success = False
            response.message = "label must not be empty"
            return response

        row = self._db.execute(
            "SELECT id FROM maps WHERE name = ?", (map_name,)
        ).fetchone()
        if row is None:
            response.success = False
            response.message = f"Map '{map_name}' not found — save the map first"
            return response

        map_id = row[0]
        self._db.execute(
            """
            INSERT INTO points (map_id, label, x, y) VALUES (?, ?, ?, ?)
            ON CONFLICT(map_id, label) DO UPDATE SET
                x = excluded.x,
                y = excluded.y
            """,
            (map_id, label, request.x, request.y),
        )
        self._db.commit()

        response.success = True
        response.message = f"Point '{label}' saved on map '{map_name}' at ({request.x}, {request.y})"
        self.get_logger().info(response.message)
        return response

    # ------------------------------------------------------------------
    # get_map_points service
    # ------------------------------------------------------------------
    def _get_map_points_cb(self, request, response):
        map_name = request.map_name.strip()
        if not map_name:
            response.success = False
            response.message = "map_name must not be empty"
            return response

        row = self._db.execute(
            "SELECT id FROM maps WHERE name = ?", (map_name,)
        ).fetchone()
        if row is None:
            response.success = False
            response.message = f"Map '{map_name}' not found"
            return response

        rows = self._db.execute(
            "SELECT label, x, y FROM points WHERE map_id = ? ORDER BY id",
            (row[0],),
        ).fetchall()

        response.success = True
        response.message = f"Found {len(rows)} point(s) for map '{map_name}'"
        response.labels = [r[0] for r in rows]
        response.x = [r[1] for r in rows]
        response.y = [r[2] for r in rows]
        return response

    # ------------------------------------------------------------------
    # get_map_with_points service
    # ------------------------------------------------------------------
    def _get_map_with_points_cb(self, request, response):
        map_name = request.map_name.strip()
        if not map_name:
            response.success = False
            response.message = "map_name must not be empty"
            return response

        map_row = self._db.execute(
            "SELECT id, resolution, origin_x, origin_y FROM maps WHERE name = ?",
            (map_name,),
        ).fetchone()
        if map_row is None:
            response.success = False
            response.message = f"Map '{map_name}' not found"
            return response

        map_id, resolution, origin_x, origin_y = map_row
        point_rows = self._db.execute(
            "SELECT label, x, y FROM points WHERE map_id = ? ORDER BY id",
            (map_id,),
        ).fetchall()

        response.success = True
        response.message = f"Map '{map_name}' with {len(point_rows)} point(s)"
        response.resolution = resolution
        response.origin_x = origin_x
        response.origin_y = origin_y
        response.labels = [r[0] for r in point_rows]
        response.x = [r[1] for r in point_rows]
        response.y = [r[2] for r in point_rows]
        return response

    # ------------------------------------------------------------------
    # get_point_coordinates service
    # ------------------------------------------------------------------
    def _get_point_coordinates_cb(self, request, response):
        map_name = request.map_name.strip()
        point_name = request.point_name.strip()

        if not map_name:
            response.success = False
            response.message = "map_name must not be empty"
            return response
        if not point_name:
            response.success = False
            response.message = "point_name must not be empty"
            return response

        row = self._db.execute(
            """
            SELECT p.x, p.y
            FROM points p
            JOIN maps m ON p.map_id = m.id
            WHERE m.name = ? AND p.label = ?
            """,
            (map_name, point_name),
        ).fetchone()

        if row is None:
            response.success = False
            response.message = f"Point '{point_name}' not found on map '{map_name}'"
            return response

        response.success = True
        response.x = row[0]
        response.y = row[1]
        response.message = f"Point '{point_name}' on map '{map_name}' at ({row[0]}, {row[1]})"
        return response

    # ------------------------------------------------------------------
    # set_slam_config service
    # ------------------------------------------------------------------
    def _set_slam_config_cb(self, request, response):
        map_name = request.map_name.strip()
        localization = request.localization

        self._db.execute(
            "INSERT OR REPLACE INTO slam_config (key, value) VALUES ('map_name', ?)",
            (map_name,),
        )
        self._db.execute(
            "INSERT OR REPLACE INTO slam_config (key, value) VALUES ('localization', ?)",
            ("true" if localization else "false",),
        )
        self._db.commit()

        # Write JSON config file for launch files
        pathlib.Path(self._maps_dir).mkdir(parents=True, exist_ok=True)
        config_path = os.path.join(self._maps_dir, "slam_config.json")
        map_file_path = os.path.join(self._maps_dir, map_name) if map_name else ""
        with open(config_path, "w") as f:
            json.dump({"map_name": map_name, "map_file": map_file_path, "localization": localization}, f)

        response.success = True
        response.message = (
            f"SLAM config saved: map='{map_name}', "
            f"mode={'localization' if localization else 'mapping'}"
        )
        self.get_logger().info(response.message)
        return response

    # ------------------------------------------------------------------
    # get_slam_config service
    # ------------------------------------------------------------------
    def _get_slam_config_cb(self, request, response):
        row_map = self._db.execute(
            "SELECT value FROM slam_config WHERE key = 'map_name'"
        ).fetchone()
        row_loc = self._db.execute(
            "SELECT value FROM slam_config WHERE key = 'localization'"
        ).fetchone()

        response.success = True
        response.map_name = row_map[0] if row_map else ""
        response.localization = (row_loc[0] == "true") if row_loc else False
        response.message = "OK"
        return response

    # ------------------------------------------------------------------
    # restart_slam service
    # ------------------------------------------------------------------
    def _restart_slam_cb(self, request, response):
        row_map = self._db.execute(
            "SELECT value FROM slam_config WHERE key = 'map_name'"
        ).fetchone()
        row_loc = self._db.execute(
            "SELECT value FROM slam_config WHERE key = 'localization'"
        ).fetchone()
        map_name = row_map[0] if row_map else ""
        localization = (row_loc[0] == "true") if row_loc else False

        if not _HAS_SLAM_TOOLBOX:
            response.success = False
            response.message = "slam_toolbox is not installed — cannot restart SLAM"
            return response

        # Reset robot pose in Gazebo before restarting SLAM
        if localization and self._set_entity_client is not None:
            self._reset_robot_pose()

        # Kill existing slam_toolbox processes
        try:
            subprocess.run(
                ["pkill", "-f", "slam_toolbox_node"],
                capture_output=True, timeout=5.0,
            )
            time.sleep(1.5)
        except Exception as e:
            self.get_logger().warn(f"pkill failed: {e}")

        # Determine params file and build command
        if localization and map_name:
            params_file = self.get_parameter("slam_params_localization").value
            map_file = os.path.join(self._maps_dir, map_name)
            executable = "localization_slam_toolbox_node"

            pg_file = map_file + ".posegraph"
            data_file = map_file + ".data"
            if not os.path.isfile(pg_file) or not os.path.isfile(data_file):
                response.success = False
                response.message = (
                    f"Posegraph files not found for map '{map_name}' "
                    f"(looked for {pg_file} and {data_file})."
                )
                return response

            # Write merged params with map_file_name
            with open(params_file, "r") as f:
                params_data = yaml.safe_load(f)

            params_data.setdefault("slam_toolbox", {}).setdefault("ros__parameters", {})
            params_data["slam_toolbox"]["ros__parameters"]["map_file_name"] = map_file

            merged_params_path = os.path.join(self._maps_dir, "_slam_localization_params.yaml")
            with open(merged_params_path, "w") as f:
                yaml.dump(params_data, f, default_flow_style=False)

            params_file = merged_params_path
        else:
            params_file = self.get_parameter("slam_params_mapping").value
            executable = "async_slam_toolbox_node"

        if not params_file:
            response.success = False
            response.message = "No SLAM params file configured"
            return response

        cmd = [
            "ros2", "run", "slam_toolbox", executable,
            "--ros-args", "--params-file", params_file,
        ]

        try:
            proc = subprocess.Popen(cmd, start_new_session=True)
            self.get_logger().info(
                f"SLAM restarted as '{executable}' (PID {proc.pid})"
            )
        except Exception as e:
            response.success = False
            response.message = f"Failed to restart SLAM: {e}"
            return response

        # Give SLAM time to publish /map before restarting Nav2
        time.sleep(3.0)
        self._restart_nav2()

        response.success = True
        response.message = (
            f"SLAM restarted as '{executable}' (PID {proc.pid}), "
            f"map='{map_name}', mode={'localization' if localization else 'mapping'}"
        )
        return response

    # ------------------------------------------------------------------
    # Nav2 lifecycle restart
    # ------------------------------------------------------------------
    def _restart_nav2(self):
        """Reset and restart Nav2 lifecycle nodes so they pick up the new map."""
        if self._nav2_lifecycle_client is None:
            return
        if not self._nav2_lifecycle_client.wait_for_service(timeout_sec=5.0):
            self.get_logger().warn("Nav2 lifecycle manager not available — skipping restart")
            return

        reset_req = ManageLifecycleNodes.Request()
        reset_req.command = ManageLifecycleNodes.Request.RESET
        self.get_logger().info("Resetting Nav2 lifecycle nodes...")
        reset_future = self._nav2_lifecycle_client.call_async(reset_req)
        rclpy.spin_until_future_complete(self, reset_future, timeout_sec=15.0)

        time.sleep(1.0)

        startup_req = ManageLifecycleNodes.Request()
        startup_req.command = ManageLifecycleNodes.Request.STARTUP
        self.get_logger().info("Starting up Nav2 lifecycle nodes...")
        startup_future = self._nav2_lifecycle_client.call_async(startup_req)
        rclpy.spin_until_future_complete(self, startup_future, timeout_sec=30.0)

    # ------------------------------------------------------------------
    # Gazebo robot pose reset
    # ------------------------------------------------------------------
    def _reset_robot_pose(self):
        """Reset the robot to (0, 0, 0) in Gazebo via /set_entity_state."""
        if self._set_entity_client is None:
            return
        if not self._set_entity_client.wait_for_service(timeout_sec=3.0):
            self.get_logger().warn("/set_entity_state not available — skipping pose reset")
            return

        from gazebo_msgs.msg import EntityState
        from geometry_msgs.msg import Pose, Twist, Point, Quaternion

        req = SetEntityState.Request()
        req.state = EntityState()
        req.state.name = "andr"
        req.state.pose = Pose(
            position=Point(x=0.0, y=0.0, z=0.1),
            orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
        )
        req.state.twist = Twist()

        future = self._set_entity_client.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=5.0)

        if future.result() is not None and future.result().success:
            self.get_logger().info("Robot pose reset to (0, 0, 0) in Gazebo")
        else:
            self.get_logger().warn("Failed to reset robot pose in Gazebo")

    # ------------------------------------------------------------------
    # OGM save helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _save_occupancy_grid(base_path: str, grid: OccupancyGrid):
        """Save an OccupancyGrid as a ROS-standard .pgm + .yaml pair."""
        width = grid.info.width
        height = grid.info.height
        resolution = grid.info.resolution
        origin = grid.info.origin.position

        data = np.array(grid.data, dtype=np.int8).reshape((height, width))
        img = np.full((height, width), 205, dtype=np.uint8)
        img[data == 0] = 254
        img[data == 100] = 0
        known = (data > 0) & (data < 100)
        img[known] = (255 - (data[known].astype(np.float32) * 255.0 / 100.0)).astype(np.uint8)
        img = np.flipud(img)

        pgm_path = base_path + ".pgm"
        yaml_path = base_path + ".yaml"

        with open(pgm_path, "wb") as f:
            header = f"P5\n{width} {height}\n255\n"
            f.write(header.encode("ascii"))
            f.write(img.tobytes())

        pgm_filename = os.path.basename(pgm_path)
        with open(yaml_path, "w") as f:
            f.write(f"image: {pgm_filename}\n")
            f.write(f"resolution: {resolution}\n")
            f.write(f"origin: [{origin.x}, {origin.y}, 0.0]\n")
            f.write("negate: 0\n")
            f.write("occupied_thresh: 0.65\n")
            f.write("free_thresh: 0.196\n")


def main(args=None):
    rclpy.init(args=args)
    node = MapServer()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
