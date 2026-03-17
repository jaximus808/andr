"""Map management service node.

Provides services to save the current SLAM map and list saved maps.
Saved maps include both the occupancy grid (.pgm/.yaml) and the
SLAM Toolbox pose graph (.posegraph/.data) for later localization.

Points of interest are stored in a SQLite database (maps.db) inside
the maps directory, with a foreign-key join from points -> maps.
"""

import os
import pathlib
import sqlite3

_MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"

import numpy as np
import rclpy
from rclpy.node import Node
from nav_msgs.msg import OccupancyGrid
from slam_toolbox.srv import SerializePoseGraph

from andr.srv import SaveMap, GetMaps, SavePoint, GetMapPoints, GetMapWithPoints

DEFAULT_MAPS_DIR = os.path.expanduser("~/andr_maps")


class MapServer(Node):
    def __init__(self):
        super().__init__("map_server")

        self.declare_parameter("maps_dir", DEFAULT_MAPS_DIR)
        self._maps_dir = self.get_parameter("maps_dir").get_parameter_value().string_value

        # Ensure maps directory exists
        pathlib.Path(self._maps_dir).mkdir(parents=True, exist_ok=True)

        # Initialise SQLite database
        self._db_path = os.path.join(self._maps_dir, "maps.db")
        self._db = sqlite3.connect(self._db_path, check_same_thread=False)
        self._init_db()

        # Cache latest occupancy grid
        self._latest_map: OccupancyGrid | None = None
        self._map_sub = self.create_subscription(
            OccupancyGrid, "/map", self._map_cb, 10
        )

        # Client for slam_toolbox serialization
        self._serialize_client = self.create_client(
            SerializePoseGraph, "/slam_toolbox/serialize_map"
        )

        # Services
        self._save_srv = self.create_service(
            SaveMap, "map_manager/save_map", self._save_map_cb
        )
        self._get_srv = self.create_service(
            GetMaps, "map_manager/get_maps", self._get_maps_cb
        )
        self._save_point_srv = self.create_service(
            SavePoint, "map_manager/save_point", self._save_point_cb
        )
        self._get_map_points_srv = self.create_service(
            GetMapPoints, "map_manager/get_map_points", self._get_map_points_cb
        )
        self._get_map_with_points_srv = self.create_service(
            GetMapWithPoints, "map_manager/get_map_with_points", self._get_map_with_points_cb
        )

        self.get_logger().info(
            f"MapServer ready — maps stored in '{self._maps_dir}'"
        )

    # ------------------------------------------------------------------
    # Database migrations
    # ------------------------------------------------------------------
    def _init_db(self):
        """Run any pending SQL migrations in version order."""
        # Bootstrap: schema_migrations table must exist before we query it.
        # Migration 001 is always applied directly so the runner can record it.
        bootstrap = _MIGRATIONS_DIR / "001_create_schema_migrations.sql"
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
    def _save_map_cb(self, request: SaveMap.Request, response: SaveMap.Response):
        name = request.map_name.strip()
        if not name:
            response.success = False
            response.message = "map_name must not be empty"
            return response

        map_path = os.path.join(self._maps_dir, name)

        # 1. Save the occupancy grid as .pgm + .yaml
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

        # 2. Upsert map row in the database
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

        # 3. Serialize SLAM Toolbox pose graph (for localization)
        if self._serialize_client.wait_for_service(timeout_sec=2.0):
            req = SerializePoseGraph.Request()
            req.filename = map_path
            future = self._serialize_client.call_async(req)
            rclpy.spin_until_future_complete(self, future, timeout_sec=10.0)
            if future.result() is None:
                self.get_logger().warn("slam_toolbox serialize call failed")
        else:
            self.get_logger().warn(
                "slam_toolbox serialize service not available — "
                "saved occupancy grid only"
            )

        response.success = True
        response.message = f"Map '{name}' saved to {map_path}"
        self.get_logger().info(response.message)
        return response

    # ------------------------------------------------------------------
    # get_maps service
    # ------------------------------------------------------------------
    def _get_maps_cb(self, request: GetMaps.Request, response: GetMaps.Response):
        # A saved map has at least a .yaml file
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
    def _save_point_cb(self, request: SavePoint.Request, response: SavePoint.Response):
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
            "INSERT INTO points (map_id, label, x, y) VALUES (?, ?, ?, ?)",
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
    def _get_map_points_cb(
        self, request: GetMapPoints.Request, response: GetMapPoints.Response
    ):
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
    def _get_map_with_points_cb(
        self, request: GetMapWithPoints.Request, response: GetMapWithPoints.Response
    ):
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
    # OGM save helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _save_occupancy_grid(base_path: str, grid: OccupancyGrid):
        """Save an OccupancyGrid as a ROS-standard .pgm + .yaml pair."""
        width = grid.info.width
        height = grid.info.height
        resolution = grid.info.resolution
        origin = grid.info.origin.position

        # Convert occupancy data (-1/0..100) to grayscale (0..255)
        data = np.array(grid.data, dtype=np.int8).reshape((height, width))
        # ROS convention: -1 = unknown (205), 0 = free (254), 100 = occupied (0)
        img = np.full((height, width), 205, dtype=np.uint8)
        img[data == 0] = 254
        img[data == 100] = 0
        # Scale known values in between
        known = (data > 0) & (data < 100)
        img[known] = (255 - (data[known].astype(np.float32) * 255.0 / 100.0)).astype(np.uint8)
        # Flip vertically (PGM row 0 is top, ROS map row 0 is bottom)
        img = np.flipud(img)

        pgm_path = base_path + ".pgm"
        yaml_path = base_path + ".yaml"

        # Write PGM (P5 binary)
        with open(pgm_path, "wb") as f:
            header = f"P5\n{width} {height}\n255\n"
            f.write(header.encode("ascii"))
            f.write(img.tobytes())

        # Write YAML metadata
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
