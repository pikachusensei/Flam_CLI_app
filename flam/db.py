# flam/db.py
import sqlite3
from contextlib import contextmanager
from datetime import datetime
import uuid
from datetime import datetime, timedelta, timezone

DB_PATH = "queue.db"

def now_iso():
    """Return current UTC time in ISO format"""
    return datetime.utcnow().isoformat() + "Z"

@contextmanager
def get_conn():
    """Context manager for SQLite connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            max_retries INTEGER DEFAULT 3,
            base_backoff REAL DEFAULT 2.0,
            next_run_at TEXT,
            last_error TEXT,
            locked_by TEXT,
            locked_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            timeout_seconds INTEGER DEFAULT 30,       
            priority INTEGER DEFAULT 0,               
            last_output TEXT                          
            duration_seconds REAL

        )
        """)

        # MIGRATIONS
        columns = conn.execute("PRAGMA table_info(jobs)").fetchall()
        col_names = [col[1] for col in columns]

        if "locked_at" not in col_names:
            conn.execute("ALTER TABLE jobs ADD COLUMN locked_at TEXT;")
            print("Added missing column: locked_at")

    
        if "timeout_seconds" not in col_names:
            conn.execute("ALTER TABLE jobs ADD COLUMN timeout_seconds INTEGER DEFAULT 30;")
            print("Added missing column: timeout_seconds")

        if "priority" not in col_names:
            conn.execute("ALTER TABLE jobs ADD COLUMN priority INTEGER DEFAULT 0;")
            print("Added missing column: priority")

        if "last_output" not in col_names:
            conn.execute("ALTER TABLE jobs ADD COLUMN last_output TEXT;")
            print("Added missing column: last_output")

        if "duration_seconds" not in col_names:
            conn.execute("ALTER TABLE jobs ADD COLUMN duration_seconds REAL;")
            print("Added missing column: duration_seconds")


    init_config()
    print("Database initialized successfully at", DB_PATH)



def enqueue_job(command, timeout_seconds=30, priority=0, next_run_at=None):
    """
    Insert job into DB. next_run_at must be an ISO string with Z (UTC) or None for ASAP.
    """
    job_id = uuid.uuid4().hex[:8]
    now = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO jobs (
                id, command, state, attempts, max_retries, base_backoff,
                next_run_at, last_error, locked_by, locked_at,
                created_at, updated_at,
                timeout_seconds, priority, last_output
            )
            VALUES (?, ?, 'pending', 0, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
        """, (
            job_id,
            command,
            3,                     # default max_retries (you can replace or read config)
            2.0,                   # default base_backoff
            next_run_at,
            now,
            now,
            timeout_seconds,
            priority,
            None                   # last_output
        ))
    return job_id

def get_job_counts():
    """Return a count of jobs grouped by state."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT state, COUNT(*) as count
            FROM jobs
            GROUP BY state
        """).fetchall()
    return {r["state"]: r["count"] for r in rows}


def list_jobs_by_state(state: str):
    """Return all jobs in a given state."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, command, attempts, max_retries, last_error
            FROM jobs
            WHERE state=?
            ORDER BY created_at
        """, (state,)).fetchall()
    return rows


def list_dead_jobs():
    """Return all jobs in Dead Letter Queue."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, command, attempts, last_error
            FROM jobs
            WHERE state='dead'
            ORDER BY updated_at DESC
        """).fetchall()
    return rows


def retry_dead_job(job_id: str):
    """Move a dead job back to pending for re-execution."""
    with get_conn() as conn:
        job = conn.execute(
            "SELECT id FROM jobs WHERE id=? AND state='dead'", (job_id,)
        ).fetchone()
        if not job:
            return False
        conn.execute("""
            UPDATE jobs
            SET state='pending',
                attempts=0,
                next_run_at=NULL,
                last_error=NULL,
                updated_at=?
            WHERE id=?
        """, (now_iso(), job_id))
    return True


def init_config():
    """Ensure config table exists and has defaults."""
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)
        defaults = {"max_retries": "3", "base_backoff": "2"}
        for k, v in defaults.items():
            conn.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (k, v))


def set_config(key: str, value: str):
    """Set or update a configuration key."""
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))


def get_config():
    """Return current configuration as dict."""
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM config").fetchall()
    return {r["key"]: r["value"] for r in rows}



def recover_stuck_jobs(timeout_seconds=60):
    """
    Move jobs stuck in 'processing' back to 'pending'.
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(seconds=timeout_seconds)
    cutoff_iso = cutoff.isoformat() + "Z"

    with get_conn() as conn:
        # Find stuck jobs
        stuck = conn.execute("""
            SELECT id FROM jobs
            WHERE state='processing'
            AND locked_at IS NOT NULL
            AND locked_at < ?
        """, (cutoff_iso,)).fetchall()

        # Requeue them
        conn.execute("""
            UPDATE jobs
            SET state='pending',
                locked_by=NULL,
                locked_at=NULL,
                updated_at=?
            WHERE state='processing'
              AND locked_at < ?
        """, (now_iso(), cutoff_iso))

    return [row["id"] for row in stuck]
