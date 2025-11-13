import sqlite3
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from flam.db import get_conn, now_iso, DB_PATH
import os

WORKER_ID = str(uuid.uuid4())[:8]


def claim_one_job():
    """Atomically pick one pending job ready to run and lock it."""
    with get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, command, attempts, max_retries, base_backoff, timeout_seconds, priority
FROM jobs
WHERE state='pending'
  AND (next_run_at IS NULL OR next_run_at <= ?)
ORDER BY priority DESC, created_at ASC
LIMIT 1

            """,
            (now_iso(),)
        ).fetchone()

        if not row:
            conn.execute("COMMIT")
            return None

        conn.execute("""
            UPDATE jobs
            SET state='processing',
                locked_by=?,
                locked_at=?,
                updated_at=?
            WHERE id=? AND state='pending'
        """, (WORKER_ID, now_iso(), now_iso(), row["id"]))

        conn.execute("COMMIT")
        return dict(row)


def run_command(cmd, timeout_seconds):
    """Execute the job command with timeout and capture output cleanly."""
    try:
        completed = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds
        )
        return (
            completed.returncode,
            completed.stdout or "",
            completed.stderr or ""
        )

    except subprocess.TimeoutExpired:
        # Return standard timeout exit code (124) + error message
        return 124, "", f"Timeout after {timeout_seconds} seconds"

    except Exception as e:
        # Any unexpected exception during subprocess
        return 1, "", str(e)



def update_job_success(job_id, output, duration):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET state='completed',
                last_output=?,
                duration_seconds=?,
                updated_at=?
            WHERE id=?
            """,
            (output[:5000], duration, now_iso(), job_id)
        )


def update_job_failure(job, output,duration):
    """Handle job failure → retry or DLQ."""
    attempts = job["attempts"] + 1
    now = now_iso()

    # Compute exponential backoff
    next_run = (
        datetime.utcnow() + timedelta(seconds=job["base_backoff"] ** attempts)
    ).replace(tzinfo=None).isoformat() + "Z"

    with get_conn() as conn:
        # Move to DLQ if retries exceeded
        if attempts > job["max_retries"]:
            conn.execute(
                """
                UPDATE jobs
                SET state='dead',
                    attempts=?,
                    last_error=?,
                    last_output=?,
                    duration_seconds=?,
                    updated_at=?
                WHERE id=?
                """,
                (attempts, "Max retries exceeded", output[:5000], duration,now, job["id"])
            )
        else:
            # Retry again
            conn.execute(
                """
                UPDATE jobs
                SET state='pending',
                    attempts=?,
                    next_run_at=?,
                    last_error=?,
                    last_output=?,
                    duration_seconds=?,
                    updated_at=?
                WHERE id=?
                """,
                (attempts, next_run, "Job failed", output[:5000],duration, now, job["id"])
            )


def worker_loop(poll_interval=1):
    print(f"Worker {WORKER_ID} started")
    start = time.time()
    while True:
        # Stop flag check
        if os.path.exists("stop.flag"):
            print(f"[{WORKER_ID}] Stop flag detected. Exiting gracefully.")
            break

        job = claim_one_job()
        if not job:
            time.sleep(poll_interval)
            continue

        jid = job["id"]
        cmd = job["command"]
        timeout_sec = job.get("timeout_seconds", 30)

        print(f"[{WORKER_ID}] Running {jid}: {cmd} (timeout={timeout_sec}s)")

        # Run the command
        code, out, err = run_command(cmd, timeout_sec)
        duration = time.time() - start
        # Combine logs safely
        combined_output = (out or "") + (err or "")

        if code == 0:
            # SUCCESS
            print(f"[{WORKER_ID}] Job {jid} completed")
            update_job_success(jid, combined_output,duration)

        else:
            # FAILURE
            print(
                f"[{WORKER_ID}] Job {jid} failed (attempt {job['attempts'] + 1})"
            )
            update_job_failure(job, combined_output or "Unknown error",duration)

        time.sleep(0.2)

