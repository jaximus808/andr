-- Stores the current SLAM configuration (selected map and operating mode).
CREATE TABLE IF NOT EXISTS slam_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO slam_config (key, value) VALUES ('map_name', '');
INSERT OR IGNORE INTO slam_config (key, value) VALUES ('localization', 'false');
