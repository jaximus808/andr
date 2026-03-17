-- Maps table: one row per saved map, stores map metadata.
CREATE TABLE IF NOT EXISTS maps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT UNIQUE NOT NULL,
    resolution REAL,
    origin_x   REAL,
    origin_y   REAL
);
