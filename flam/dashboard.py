from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from flam.db import get_conn, now_iso

app = FastAPI(title="QueueCTL Dashboard")


# ============================================================
#  GLOBAL CSS (modern dashboard styling)
# ============================================================
BASE_CSS = """
<style>
    body {
        font-family: 'Inter', sans-serif;
        margin: 0;
        padding: 25px;
        background: #f5f7fb;
    }

    h1, h2, h3 {
        font-weight: 700;
        color: #222;
        margin-bottom: 10px;
    }

    a {
        color: #5a4fff;
        text-decoration: none;
        font-weight: 600;
    }
    a:hover { text-decoration: underline; }

    .cards {
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        margin-bottom: 25px;
    }

    .card {
        background: white;
        padding: 16px 22px;
        border-radius: 12px;
        box-shadow: 0 3px 12px rgba(0,0,0,0.06);
        font-size: 17px;
        min-width: 160px;
        flex-grow: 1;
    }

    .badge {
        padding: 5px 10px;
        border-radius: 6px;
        font-size: 13px;
        font-weight: 700;
        color: white;
    }
    .pending { background: #f7b731; }
    .processing { background: #3498db; }
    .completed { background: #2ecc71; }
    .dead { background: #e74c3c; }
    .scheduled { background: #8e44ad; }

    table {
        width: 100%;
        border-collapse: collapse;
        background: white;
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 3px 15px rgba(0,0,0,0.08);
    }

    th {
        background: #5a4fff;
        color: white;
        padding: 12px;
        text-align: left;
        font-size: 14px;
    }

    td {
        padding: 10px 14px;
        border-bottom: 1px solid #eee;
        font-size: 15px;
        color: #333;
    }

    tr:hover td {
        background: #f0f2ff;
    }

    .nav {
        margin-bottom: 20px;
        padding-bottom: 8px;
        border-bottom: 2px solid #ddd;
        font-size: 15px;
    }

    .button {
        padding: 10px 16px;
        border-radius: 8px;
        background: #5a4fff;
        color: white;
        font-size: 14px;
        border: none;
        cursor: pointer;
        display: inline-block;
        margin-right: 8px;
    }
    .button:hover {
        background: #483ae0;
    }

    .log-box {
        background: #111;
        color: #0f0;
        padding: 16px;
        border-radius: 8px;
        font-family: monospace;
        white-space: pre-wrap;
        font-size: 14px;
    }

    .output-box {
        background: #eee;
        padding: 15px;
        white-space: pre-wrap;
        border-radius: 8px;
        font-family: monospace;
        font-size: 14px;
    }
</style>
"""


# ============================================================
#  METRICS FETCHING
# ============================================================
def fetch_metrics():
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

        recent_jobs = conn.execute("""
            SELECT id, command, state, attempts, duration_seconds, last_output, updated_at
            FROM jobs
            ORDER BY updated_at DESC
            LIMIT 20
        """).fetchall()

    return {
        "total": total,
        "pending": pending,
        "processing": processing,
        "completed": completed,
        "dead": dead,
        "scheduled": scheduled,
        "recent": recent_jobs,
    }


# ============================================================
#  DASHBOARD HOME PAGE
# ============================================================
@app.get("/", response_class=HTMLResponse)
def dashboard():
    m = fetch_metrics()

    html = f"""
    <html>
    <head>
        <title>QueueCTL Dashboard</title>
        <meta http-equiv="refresh" content="3">
        {BASE_CSS}
    </head>

    <body>
        <h1>📊 QueueCTL Dashboard</h1>

        <div class='nav'>
            <a href='/jobs/all'>All</a> |
            <a href='/jobs/pending'>Pending</a> |
            <a href='/jobs/processing'>Processing</a> |
            <a href='/jobs/completed'>Completed</a> |
            <a href='/jobs/dead'>Dead</a> |
            <a href='/jobs/scheduled'>Scheduled</a>
        </div>

        <div class='cards'>
            <div class='card'>Total: {m['total']}</div>
            <div class='card'>Pending: {m['pending']}</div>
            <div class='card'>Processing: {m['processing']}</div>
            <div class='card'>Completed: {m['completed']}</div>
            <div class='card'>Dead: {m['dead']}</div>
            <div class='card'>Scheduled: {m['scheduled']}</div>
        </div>

        <h2>🧾 Recent Jobs</h2>

        <table>
            <tr>
                <th>ID</th>
                <th>Command</th>
                <th>Status</th>
                <th>Attempts</th>
                <th>Duration</th>
                <th>Output</th>
                <th>Updated</th>
                <th>View</th>
            </tr>
    """

    for job in m["recent"]:
        badge = f"<span class='badge {job['state']}'>{job['state']}</span>"
        output_snip = (job["last_output"] or "").replace("\n", " ")[:40]
        duration = f"{job['duration_seconds']:.2f}s" if job["duration_seconds"] else "-"

        html += f"""
            <tr>
                <td>{job['id']}</td>
                <td>{job['command']}</td>
                <td>{badge}</td>
                <td>{job['attempts']}</td>
                <td>{duration}</td>
                <td>{output_snip}</td>
                <td>{job['updated_at']}</td>
                <td><a href='/job/{job["id"]}'>Open</a></td>
            </tr>
        """

    html += "</table></body></html>"
    return html


# ============================================================
#  LIST JOBS BY STATE
# ============================================================
@app.get("/jobs/{state}", response_class=HTMLResponse)
def list_jobs(state: str):
    valid = ["pending", "processing", "completed", "dead", "scheduled", "all"]

    if state not in valid:
        return HTMLResponse(f"<h1>Invalid state: {state}</h1>")

    with get_conn() as conn:
        if state == "all":
            rows = conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 50").fetchall()
        elif state == "scheduled":
            rows = conn.execute(
                "SELECT * FROM jobs WHERE next_run_at > ? ORDER BY next_run_at",
                (now_iso(),)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE state=? ORDER BY updated_at DESC",
                (state,)
            ).fetchall()

    html = f"""
    <html>
    <head>{BASE_CSS}</head>
    <body>
        <a href='/'>← Back</a>
        <h1>Jobs — {state.upper()}</h1>

        <table>
            <tr>
                <th>ID</th>
                <th>Command</th>
                <th>Status</th>
                <th>Attempts</th>
                <th>Updated</th>
                <th>View</th>
            </tr>
    """

    for j in rows:
        badge = f"<span class='badge {j['state']}'>{j['state']}</span>"

        html += f"""
            <tr>
                <td>{j['id']}</td>
                <td>{j['command']}</td>
                <td>{badge}</td>
                <td>{j['attempts']}</td>
                <td>{j['updated_at']}</td>
                <td><a href='/job/{j["id"]}'>Open</a></td>
            </tr>
        """

    html += "</table></body></html>"
    return html


# ============================================================
#  JOB DETAIL PAGE
# ============================================================
@app.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(job_id: str):
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    if not job:
        return HTMLResponse("<h1>Job not found</h1>")

    output = (job["last_output"] or "").replace("<", "&lt;")

    html = f"""
    <html>
    <head>{BASE_CSS}</head>
    <body>
        <a href='/'>← Back</a>
        <h1>Job {job_id}</h1>

        <div class='cards'>
            <div class='card'><b>Command:</b> {job['command']}</div>
            <div class='card'><b>Status:</b> <span class='badge {job['state']}'>{job['state']}</span></div>
            <div class='card'><b>Attempts:</b> {job['attempts']}</div>
            <div class='card'><b>Duration:</b> {job['duration_seconds'] or "-"} s</div>
        </div>

        <h3>Output</h3>
        <div class="output-box">{output}</div>

        <br><br>
        <a href="/job/{job_id}/retry"><button class="button">Retry Job</button></a>
        <a href="/job/{job_id}/tail"><button class="button">Tail Logs</button></a>
    </body></html>
    """

    return HTMLResponse(html)


# ============================================================
#  RETRY JOB
# ============================================================
@app.get("/job/{job_id}/retry", response_class=HTMLResponse)
def job_retry(job_id: str):
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return HTMLResponse("Job not found")

        conn.execute("""
            UPDATE jobs
            SET state='pending',
                attempts=0,
                next_run_at=NULL,
                last_error=NULL,
                updated_at=?
            WHERE id=?
        """, (now_iso(), job_id))

    return HTMLResponse(f"""
    <html><head>{BASE_CSS}</head><body>
    <h1>Job {job_id} requeued!</h1>
    <a href='/job/{job_id}'>Back</a>
    </body></html>
    """)


# ============================================================
#  TAIL LOGS
# ============================================================
@app.get("/job/{job_id}/tail", response_class=HTMLResponse)
def job_tail(job_id: str):
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    if not job:
        return HTMLResponse("Job not found")

    output = (job["last_output"] or "").replace("<", "&lt;")

    html = f"""
    <html>
    <head>
        <meta http-equiv="refresh" content="2">
        {BASE_CSS}
    </head>
    <body>
        <a href='/job/{job_id}'>← Back</a>
        <h2>Tailing Logs for {job_id}</h2>

        <div class='log-box'>{output}</div>
    </body></html>
    """

    return html
