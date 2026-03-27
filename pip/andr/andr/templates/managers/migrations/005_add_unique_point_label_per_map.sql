-- Add UNIQUE(map_id, label) constraint to points table.
-- SQLite does not support ADD CONSTRAINT, so we recreate the table.

BEGIN;

CREATE TABLE IF NOT EXISTS points_new (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id INTEGER NOT NULL REFERENCES maps(id),
    label  TEXT NOT NULL,
    x      REAL NOT NULL,
    y      REAL NOT NULL,
    UNIQUE (map_id, label)
);

INSERT OR IGNORE INTO points_new (id, map_id, label, x, y)
    SELECT id, map_id, label, x, y FROM points;

DROP TABLE points;

ALTER TABLE points_new RENAME TO points;

COMMIT;
