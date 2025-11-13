import os
import multiprocessing
import typer
from datetime import datetime, timedelta, timezone

from flam.db import (
    init_db,
    enqueue_job,
    get_job_counts,
    list_jobs_by_state,
    list_dead_jobs,
    retry_dead_job,
    now_iso,
    get_conn
)
from flam.worker import worker_loop

# ============================================================
# MAIN APP + CONFIG SUBCOMMAND GROUP
# ============================================================

app = typer.Typer(help="QueueCTL - Background job queue system")
config_app = typer.Typer(help="Manage QueueCTL configuration")
app.add_typer(config_app, name="config")

# ============================================================
# CONFIG COMMANDS (PROPER SUBCOMMAND STYLE)
# ============================================================

from flam.config import load_config, save_config


@config_app.command("show")
def config_show():
    """Show full configuration."""
    cfg = load_config()
    typer.echo("Current Configuration:")
    for k, v in cfg.items():
        typer.echo(f"- {k}: {v}")


@config_app.command("get")
def config_get(key: str):
    """Get a configuration value."""
    cfg = load_config()
    if key not in cfg:
        typer.echo(f"Unknown config key: {key}")
        raise typer.Exit(1)
    typer.echo(f"{key} = {cfg[key]}")


@config_app.command("set")
def config_set(key: str, value: str):
    """Set a configuration value."""
    cfg = load_config()

    # try converting to number
    try:
        if "." in value:
            value = float(value)
        else:
            value = int(value)
    except:
        pass

    cfg[key] = value
    save_config(cfg)
    typer.echo(f"Updated {key} = {value}")

# ============================================================
# INIT DB
# ============================================================

@app.command("init")
def init():
    """Initialize database + config."""
    init_db()
    typer.echo("Database initialized (or already up-to-date).")

# ============================================================
# UTIL
# ============================================================

def parse_run_at(run_at: str):
    """Parse ISO-8601 UTC timestamps."""
    if not run_at:
        return None
    try:
        if run_at.endswith("Z"):
            dt = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(run_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception as e:
        raise ValueError(f"Invalid run_at format. Use ISO like 2025-12-01T10:00:00Z. Error: {e}")

# ============================================================
# ENQUEUE
# ============================================================

@app.command("enqueue")
def enqueue(
    command: str,
    timeout: int = typer.Option(30),
    priority: int = typer.Option(0),
    run_at: str = typer.Option(None),
    delay: int = typer.Option(None)
):
    """Enqueue a new job."""
    if run_at and delay is not None:
        typer.echo("Use either --run-at or --delay, not both.", err=True)
        raise typer.Exit(2)

    next_run_iso = None

    if run_at:
        next_run_iso = parse_run_at(run_at)
    elif delay:
        dt = datetime.utcnow() + timedelta(seconds=delay)
        next_run_iso = dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

    job_id = enqueue_job(command, timeout, priority, next_run_iso)

    typer.echo(
        f"Enqueued job {job_id}: {command} "
        f"(run_at={next_run_iso or 'ASAP'}, timeout={timeout}s, priority={priority})"
    )

# ============================================================
# WORKER START / STOP
# ============================================================

@app.command("worker-start")
def worker_start(count: int = typer.Option(1)):
    """Start worker processes."""
    from flam.db import recover_stuck_jobs

    stuck = recover_stuck_jobs(timeout_seconds=60)
    if stuck:
        typer.echo(f"Recovered stuck jobs: {', '.join(stuck)}")
    else:
        typer.echo("No stuck jobs found.")

    if os.path.exists("stop.flag"):
        os.remove("stop.flag")

    procs = []
    for _ in range(count):
        p = multiprocessing.Process(target=worker_loop)
        p.start()
        procs.append(p)

    typer.echo(f"Started {count} worker(s). Press Ctrl+C to stop.")

    try:
        for p in procs:
            p.join()
    except KeyboardInterrupt:
        typer.echo("\nStopping workers gracefully...")
        for p in procs:
            p.terminate()


@app.command("worker-stop")
def worker_stop():
    """Signal workers to stop."""
    with open("stop.flag", "w") as f:
        f.write("1")
    typer.echo("Stop signal sent. Workers will stop soon.")

# ============================================================
# STATUS
# ============================================================

@app.command("status")
def status():
    """Show counts of job states."""
    counts = get_job_counts()
    typer.echo("\nJob Status Summary")
    for state in ["pending", "processing", "completed", "dead"]:
        typer.echo(f"  {state:<12} : {counts.get(state, 0)}")
    typer.echo("")

# ============================================================
# LIST JOBS
# ============================================================

@app.command("list")
def list_jobs(state: str = typer.Option("pending")):
    """List jobs by state."""
    rows = list_jobs_by_state(state)
    if not rows:
        typer.echo(f"No jobs found in state '{state}'")
        return

    typer.echo(f"\nJobs in state '{state}':")
    for r in rows:
        msg = (
            f"{r['id']} | {r['command']} "
            f"| attempts={r['attempts']}/{r['max_retries']} "
            f"| run_at={r['next_run_at'] or 'ASAP'}"
        )
        if r["last_error"]:
            msg += f" | error={r['last_error'][:60]}"
        typer.echo(msg)

# ============================================================
# DLQ
# ============================================================

@app.command("dlq")
def dlq(action: str = typer.Argument("list"), job_id: str = typer.Argument(None)):
    """View or retry dead jobs."""
    if action == "list":
        rows = list_dead_jobs()
        if not rows:
            typer.echo("No dead jobs.")
            return

        typer.echo("\nDead Letter Queue:")
        for r in rows:
            typer.echo(
                f"{r['id']} | {r['command']} "
                f"| attempts={r['attempts']} | error={r['last_error'][:80]}"
            )
        return

    if action == "retry" and job_id:
        ok = retry_dead_job(job_id)
        if ok:
            typer.echo(f"Retried job {job_id} (moved back to pending).")
        else:
            typer.echo("Job not found.")
        return

    typer.echo("Usage: queuectl dlq list | queuectl dlq retry <job_id>")

# ============================================================
# LOGS
# ============================================================

@app.command("logs")
def logs(job_id: str):
    """Show job logs."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, command, state, last_output FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()

        if not row:
            typer.echo("Job not found")
            raise typer.Exit()

        typer.echo(f"Logs for job {job_id}")
        typer.echo(f"Command: {row['command']}")
        typer.echo(f"State:   {row['state']}")
        typer.echo("----- OUTPUT BEGIN -----")
        typer.echo(row["last_output"] or "(no output)")
        typer.echo("----- OUTPUT END -----")

# ============================================================
# METRICS
# ============================================================

@app.command("metrics")
def metrics():
    """Show overall system metrics."""
    with get_conn() as conn:

        total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='pending'").fetchone()[0]
        processing = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='processing'").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='completed'").fetchone()[0]
        dead = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='dead'").fetchone()[0]

        scheduled = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE state='pending' AND next_run_at > ?",
            (now_iso(),)
        ).fetchone()[0]

        total_retries = conn.execute("SELECT SUM(attempts) FROM jobs").fetchone()[0] or 0
        avg_retries = conn.execute("SELECT AVG(attempts) FROM jobs").fetchone()[0] or 0

        avg_duration = conn.execute("SELECT AVG(duration_seconds) FROM jobs").fetchone()[0] or 0
        max_duration = conn.execute("SELECT MAX(duration_seconds) FROM jobs").fetchone()[0] or 0
        min_duration = conn.execute("SELECT MIN(duration_seconds) FROM jobs WHERE duration_seconds > 0").fetchone()[0] or 0

    typer.echo("\n📊 Queue Metrics")
    typer.echo("==========================")
    typer.echo(f"Total Jobs:        {total}")
    typer.echo(f"  Pending:         {pending}")
    typer.echo(f"  Processing:      {processing}")
    typer.echo(f"  Completed:       {completed}")
    typer.echo(f"  Dead (DLQ):      {dead}")
    typer.echo("")
    typer.echo(f"Scheduled Jobs:    {scheduled}")
    typer.echo("")
    typer.echo(f"Total Retries:     {total_retries}")
    typer.echo(f"Avg Retries/Job:   {avg_retries:.2f}")
    typer.echo("")
    typer.echo(f"Avg Duration:      {avg_duration:.3f}s")
    typer.echo(f"Fastest Job:       {min_duration:.3f}s")
    typer.echo(f"Slowest Job:       {max_duration:.3f}s")
    typer.echo("==========================")

# ============================================================
# DASHBOARD
# ============================================================

@app.command("dashboard")
def dashboard():
    """Start the QueueCTL dashboard."""
    import uvicorn
    typer.echo("Starting dashboard at http://localhost:8000 ...")
    uvicorn.run("flam.dashboard:app", host="127.0.0.1", port=8000, reload=False)

# ============================================================
# VERSION
# ============================================================

@app.command("version")
def version():
    typer.echo("QueueCTL version 0.0.1")


if __name__ == "__main__":
    app()
