-- Points table: named points of interest linked to a map.
CREATE TABLE IF NOT EXISTS points (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    label  TEXT NOT NULL,
    x      REAL NOT NULL,
    y      REAL NOT NULL
);
