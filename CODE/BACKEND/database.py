import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator
from .config import DATABASE_PATH, ensure_directories

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS analysis_jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','cancelled','failed')),
    input_video TEXT,
    output_video TEXT,
    result_csv TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    progress REAL NOT NULL DEFAULT 0,
    frame INTEGER NOT NULL DEFAULT 0,
    total_frames INTEGER NOT NULL DEFAULT 0,
    runtime_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS traffic_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    frame INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    car_count INTEGER NOT NULL DEFAULT 0,
    motorcycle_count INTEGER NOT NULL DEFAULT 0,
    bus_count INTEGER NOT NULL DEFAULT 0,
    truck_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    density TEXT NOT NULL,
    north_count INTEGER NOT NULL DEFAULT 0,
    east_count INTEGER NOT NULL DEFAULT 0,
    south_count INTEGER NOT NULL DEFAULT 0,
    west_count INTEGER NOT NULL DEFAULT 0,
    north_density REAL NOT NULL DEFAULT 0,
    east_density REAL NOT NULL DEFAULT 0,
    south_density REAL NOT NULL DEFAULT 0,
    west_density REAL NOT NULL DEFAULT 0,
    selected_phase TEXT,
    signal_state TEXT,
    green_seconds REAL,
    remaining_seconds REAL,
    queue_proxy REAL DEFAULT 0,
    north_queue REAL DEFAULT 0,
    east_queue REAL DEFAULT 0,
    south_queue REAL DEFAULT 0,
    west_queue REAL DEFAULT 0,
    FOREIGN KEY(job_id) REFERENCES analysis_jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_jobs_user ON analysis_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON analysis_jobs(status);
CREATE INDEX IF NOT EXISTS idx_results_job_frame ON traffic_results(job_id, frame);
"""


def _column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn):
    # Safe migration for databases created by the previous project version.
    required = {
        "progress": "REAL NOT NULL DEFAULT 0",
        "frame": "INTEGER NOT NULL DEFAULT 0",
        "total_frames": "INTEGER NOT NULL DEFAULT 0",
        "runtime_json": "TEXT NOT NULL DEFAULT '{}'",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
    }
    existing = _column_names(conn, "analysis_jobs")
    for name, definition in required.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE analysis_jobs ADD COLUMN {name} {definition}")
    traffic_required = {
        "north_density": "REAL NOT NULL DEFAULT 0",
        "east_density": "REAL NOT NULL DEFAULT 0",
        "south_density": "REAL NOT NULL DEFAULT 0",
        "west_density": "REAL NOT NULL DEFAULT 0",
        "selected_phase": "TEXT",
        "signal_state": "TEXT",
        "green_seconds": "REAL",
        "remaining_seconds": "REAL",
        "queue_proxy": "REAL DEFAULT 0",
        "north_queue": "REAL DEFAULT 0",
        "east_queue": "REAL DEFAULT 0",
        "south_queue": "REAL DEFAULT 0",
        "west_queue": "REAL DEFAULT 0",
    }
    existing = _column_names(conn, "traffic_results")
    for name, definition in traffic_required.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE traffic_results ADD COLUMN {name} {definition}")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("UPDATE analysis_jobs SET updated_at=? WHERE updated_at='' OR updated_at IS NULL", (now,))


def get_connection() -> sqlite3.Connection:
    ensure_directories()
    conn = sqlite3.connect(DATABASE_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def initialize_database() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def database_connection() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
