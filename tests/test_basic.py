import subprocess
import time
import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = "queue.db"


# Helper to run a command and capture output
def run(cmd):
    result = subprocess.run(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    return result.stdout.strip()


print("\n===== TEST 1: INIT DB =====")

if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

out = run("python -m flam.cli init")
print(out)

assert "initialized" in out.lower(), f"DB init failed: {out}"
print("✔ DB initialized")


print("\n===== TEST 2: ENQUEUE JOB =====")

out = run('python -m flam.cli enqueue "echo hello"')
print(out)
job_id = out.split()[2].replace(":", "")
print("✔ Enqueue OK / Job =", job_id)


print("\n===== TEST 3: WORKER PROCESSING =====")

p = subprocess.Popen("python -m flam.cli worker-start --count 1", shell=True)
time.sleep(3)
p.terminate()

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

state = con.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
assert state == "completed", f"Job not completed, state={state}"
print("✔ Worker completed the job")


print("\n===== TEST 4: INVALID → DLQ =====")

run("python -m flam.cli init")
time.sleep(0.3)

out = run('python -m flam.cli enqueue "invalid_command_123"')
print(out)
dead_id = out.split()[2].replace(":", "")

p = subprocess.Popen("python -m flam.cli worker-start --count 1", shell=True)

deadline = time.time() + 20
state = None

while time.time() < deadline:
    row = con.execute("SELECT state FROM jobs WHERE id=?", (dead_id,)).fetchone()
    if row and row[0] == "dead":
        state = "dead"
        break
    time.sleep(1)

p.terminate()

assert state == "dead", f"DLQ test failed → last state={row}"
print("✔ Invalid job moved to DLQ")


print("\n===== TEST 5: PERSISTENCE AFTER RESTART =====")

rows = con.execute("SELECT id FROM jobs").fetchall()
assert len(rows) >= 2
print("✔ DB persisted between runs")


# -------------------------------------------------------------------
# BONUS TESTS
# -------------------------------------------------------------------

print("\n===== BONUS TEST: PRIORITY ORDERING =====")

os.remove(DB_PATH)
run("python -m flam.cli init")

run('python -m flam.cli enqueue "echo LOW" --priority 1')
run('python -m flam.cli enqueue "echo HIGH" --priority 10')

p = subprocess.Popen("python -m flam.cli worker-start --count 1", shell=True)
time.sleep(3)
p.terminate()

con = sqlite3.connect(DB_PATH)
con.row_factory = sqlite3.Row

rows = con.execute("SELECT command FROM jobs ORDER BY updated_at ASC").fetchall()
assert rows[0]["command"] == "echo HIGH", "High priority job did not execute first"
print("✔ Priority queueing works")


print("\n===== BONUS TEST: SCHEDULED JOB =====")

future = (datetime.utcnow() + timedelta(seconds=8)).isoformat() + "Z"
out = run(f'python -m flam.cli enqueue "echo FUTURE" --run-at "{future}"')
future_id = out.split()[2].replace(":", "")

p = subprocess.Popen("python -m flam.cli worker-start --count 1", shell=True)

time.sleep(3)  # shouldn't run yet

state = con.execute("SELECT state FROM jobs WHERE id=?", (future_id,)).fetchone()[0]
assert state == "pending", "Scheduled job executed too early!"

p.terminate()
print("✔ Scheduled jobs wait until the correct timestamp")


print("\n===== BONUS TEST: TIMEOUT HANDLING =====")

out = run('python -m flam.cli enqueue "timeout /T 5" --timeout 1')
timeout_id = out.split()[2].replace(":", "")

p = subprocess.Popen("python -m flam.cli worker-start --count 1", shell=True)
time.sleep(4)
p.terminate()

row = con.execute("SELECT last_error FROM jobs WHERE id=?", (timeout_id,)).fetchone()
assert "Timeout" in (row[0] or ""), "Timeout was not detected"
print("✔ Timeout killing works")


print("\n===== BONUS TEST: OUTPUT LOGGING =====")

out = run('python -m flam.cli enqueue "echo LOG_OUTPUT_TEST"')
log_id = out.split()[2].replace(":", "")

p = subprocess.Popen("python -m flam.cli worker-start --count 1", shell=True)
time.sleep(2)
p.terminate()

row = con.execute("SELECT last_output FROM jobs WHERE id=?", (log_id,)).fetchone()
assert "LOG_OUTPUT_TEST" in (row[0] or ""), "Output not logged into DB"

print("✔ Output logging works")


print("\n===== BONUS TEST: CONFIG GET/SET =====")

run("python -m flam.cli config set max_retries 9")
out = run("python -m flam.cli config get max_retries")

assert "9" in out, "Config set/get failed"
print("✔ Config updated & retrieved correctly")


# ---------------------------------------------------------------------

print("\n===== ALL TESTS COMPLETED SUCCESSFULLY =====\n")
