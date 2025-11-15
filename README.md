# QueueCTL — CLI Background Job Queue (Python + SQLite)

**QueueCTL** is a lightweight, production-minded background job queue implemented in Python.
It supports multiple workers, retries with exponential backoff, a Dead Letter Queue (DLQ),
scheduled jobs, priorities, job timeouts, output logging, metrics, and a minimal web dashboard.

---

# Live Demo Video
[Video](https://drive.google.com/file/d/1i0rPIk8873nQvkQTw1mLWF_l0db6yrrg/view?usp=sharing)
---
## Table of contents

- [Quick summary](#quick-summary)  
- [Prerequisites](#prerequisites)  
- [Install & setup (local)](#install--setup-local)  
- [CLI usage examples](#cli-usage-examples)  
- [Commands reference](#commands-reference)  
- [How it works — architecture & lifecycle](#how-it-works---architecture--lifecycle)  
- [Database schema (important columns)](#database-schema-important-columns)  
- [Configuration](#configuration)  
- [Testing](#testing)  
- [Dashboard](#dashboard)  
- [Design choices & trade-offs](#design-choices--trade-offs)  
- [Troubleshooting](#troubleshooting)  
- [License](#license)

---

## Quick summary

- Language: **Python 3.10+**  
- Storage: **SQLite** (`queue.db`) — simple, persistent, no external server required  
- CLI framework: **Typer**  
- Dashboard: **FastAPI + simple HTML UI**  
- Concurrency: Python `multiprocessing` workers with DB-level locking

---

## Prerequisites

- Python 3.10 or newer  
- `git` (optional, but recommended)  
- On Windows: PowerShell recommended; on Unix: bash / zsh

---

## Install & setup (local)

1. Clone repo:
```bash
git clone https://github.com/your-username/your-repo.git
cd your-repo
```

2. Create and activate virtual environment:
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate
```

3. Install dependencies and package (editable):
```bash
pip install -r requirements.txt
pip install -e .
```

This installs a CLI entry point `queuectl` (if using venv ensure `venv\Scripts` is active/available on PATH on Windows).

4. Initialize DB:
```bash
queuectl init
```

---

## CLI usage examples

Enqueue a simple command:
```bash
queuectl enqueue "echo 'Hello Queue'"
```

Start 2 workers (in a terminal):
```bash
queuectl worker-start --count 2
```

Stop workers (signals via stop.flag):
```bash
queuectl worker-stop
```

Show config:
```bash
queuectl config show
```

List pending jobs:
```bash
queuectl list --state pending
```

View DLQ:
```bash
queuectl dlq list
```

Retry DLQ job:
```bash
queuectl dlq retry <job_id>
```

Get logs for a job:
```bash
queuectl logs <job_id>
```

Start dashboard:
```bash
queuectl dashboard
# open http://localhost:8000
```

Get metrics:
```bash
queuectl metrics
```

---

## Commands reference (short)

- `queuectl init` — create/migrate SQLite DB  
- `queuectl enqueue "<command>" [--timeout N] [--priority P] [--run-at ISO] [--delay N]`  
- `queuectl worker-start --count N`  
- `queuectl worker-stop`  
- `queuectl status`  
- `queuectl list --state <pending|processing|completed|dead|all>`  
- `queuectl dlq list` / `queuectl dlq retry <id>`  
- `queuectl logs <id>`  
- `queuectl metrics`  
- `queuectl dashboard`  
- `queuectl config show|get|set`

---

## How it works — architecture & lifecycle

### Job lifecycle
- **pending** — waiting to be picked (may have `next_run_at` for scheduling)  
- **processing** — currently locked by a worker (`locked_by`, `locked_at`)  
- **completed** — success  
- **failed** — (intermediate) job failed but will be requeued (not stored as `failed` in final)  
- **dead** — moved to Dead Letter Queue after exhausting `max_retries`

### Claiming jobs (race-free)
Workers claim jobs using an **atomic DB transaction** (`BEGIN IMMEDIATE`) and then update the job row to `processing` with `locked_by` and `locked_at`. That prevents duplicate processing.

### Retry & exponential backoff
When a job fails:
- `attempts` increments
- If `attempts` > `max_retries` → move to `dead`
- Else compute:
  ```
  delay_seconds = base_backoff ** attempts
  next_run_at = now + delay_seconds
  ```
  set `state = pending` and `next_run_at` for requeue.

### Timeout handling
Jobs run in subprocess with `timeout`. If timeout triggers, the worker logs a timeout error and applies retry logic.

### Persistence
All job data (state, attempts, timestamps, last_output) stored in SQLite → persists across restarts.

---

## Database schema (important columns)

Each job row includes:
```text
id TEXT PRIMARY KEY
command TEXT
state TEXT                  -- pending, processing, completed, dead
attempts INTEGER
max_retries INTEGER
base_backoff REAL
next_run_at TEXT (ISO)
last_error TEXT
last_output TEXT
locked_by TEXT
locked_at TEXT
duration_seconds REAL
timeout_seconds INTEGER
priority INTEGER
created_at TEXT
updated_at TEXT
```

`next_run_at` is used for scheduling and backoffs.  
`priority` sorts jobs: higher first.

---

## Configuration

Config lives in `config.json`. Defaults:
```json
{
  "max_retries": 3,
  "base_backoff": 2,
  "default_timeout": 30,
  "poll_interval": 1,
  "priority_default": 0
}
```

Commands:
```bash
queuectl config show
queuectl config get max_retries
queuectl config set max_retries 5
```

---

## Testing

`tests/test_basic.py` contains an integration-style suite that validates:
- DB init
- Enqueue
- Worker runs job
- Invalid command retries and lands in DLQ
- Persistence

Run:
```bash
python tests/test_basic.py
```

(If tests fail because a DB or worker is running, stop worker(s) and re-run.)

**Suggested additional tests** (bonus):
- Priority ordering: enqueue different priority jobs and ensure higher priority runs first.  
- Scheduled job: enqueue with `--run-at` in the future and ensure it doesn't run early.  
- Timeout: enqueue a long-running job with low `--timeout` and ensure it times out and logs correctly.

---

## Dashboard

Start the web UI:
```bash
queuectl dashboard
```
Visit `http://localhost:8000`.

Features:
- Job summary cards
- Recent jobs table
- State filters
- Job detail with output & retry button
- Tail logs page (auto-refresh)
- Dark mode support
- Workers page (shows workers if implemented)

---

## Implementation notes & trade-offs

- **SQLite chosen**: simple, embedded, requires no server, perfect for a single-host developer test and small-scale usage. For distributed heavy loads use PostgreSQL or Redis-backed queues.
- **Atomic locking**: `BEGIN IMMEDIATE` ensures no two workers double pick. This is simple and robust for SQLite.
- **Subprocess execution**: simple and cross-platform (`shell=True`). For production you'd use sandboxing.
- **Backoff**: `base_backoff ** attempts` — configurable.
- **No external broker**: design is intentionally minimal.

---

## Troubleshooting & tips

- If `queuectl` command not found on Windows, ensure you activated the venv and `venv\Scripts` is on PATH:
  ```powershell
  .\venv\Scripts\activate
  .\venv\Scripts\queuectl.exe init
  ```
- If CLI entrypoint not created after `pip install -e .`, confirm `pyproject.toml` is at the project root and `pip install -e .` was run from that root.
- If worker is stuck in `processing` (worker crash), restart and run:
  ```bash
  queuectl worker-start --count 1
  ```
  The CLI recovers stuck jobs that are older than configured threshold on worker start.

---

## Files to include in your repository

- `flam/` package (all `.py` files)  
- `pyproject.toml` (entry point)  
- `requirements.txt`  
- `tests/test_basic.py`  
- `README.md` (this file)  
- `.gitignore` (ignore venv, queue.db, egg-info, logs)

---

## Example usage (combined)

Start everything locally:

```bash
# in terminal 1
queuectl init
queuectl dashboard

# in terminal 2
queuectl worker-start --count 2

# in terminal 3
queuectl enqueue "echo Hello world" --priority 5
queuectl enqueue "invalid_command"      # will eventually go to DLQ
```

Monitor dashboard at `http://localhost:8000` and tail logs:
```bash
queuectl logs <job_id>
```

---

## License

MIT

---

## Author
Shreyansh Singh

