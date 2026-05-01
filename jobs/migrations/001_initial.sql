-- Phase 12 v3 — initial schema marker.
--
-- huey owns its own SQLite tables (kv, schedule, task). This file is
-- a placeholder for future home-tools-managed schema (e.g., a per-Job
-- audit log) that lives in the same db file as huey's tables.
--
-- Apply this from `bash jobs/install.sh init` if you ever land schema.
-- v1 has no extra tables, just the version marker.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO schema_version(version) VALUES (1);
