# Build Guide

How the Workload Management System is put together, and how to run, demo, and
extend it. Maps directly to the proposal's milestones.

---

## Run it

```bash
pip install -r requirements.txt
python run.py          # http://127.0.0.1:5000
# or
python benchmark.py    # headless 4-scheduler comparison
```

No accounts, API keys, or Spotify Premium required — SQLite + mock providers.

---

## The job lifecycle (end to end)

1. **Submit (Producer).** `Engine.submit` builds a `Job` (state `NEW`), persists
   it to the `JOB` table for durability, moves it to `QUEUED`, and drops it on
   the in-memory ready queue. This decouples intake from execution.
2. **Schedule.** A worker thread asks the active **Scheduler** to `select()` the
   next job from the ready queue:
   - **FCFS** — earliest arrival.
   - **SJF** — smallest remaining service time.
   - **Priority** — lowest priority number.
   - **Round Robin** — oldest last-enqueue; runs one quantum then rotates.
   - **FairShare** — the user with the least service so far (equal share per user).
   - **WFQ** — like FairShare but weighted by tier (PREMIUM served more). Uses
     per-user virtual time; the engine calls `scheduler.notify_run()` after each
     slice to advance it.
3. **Acquire resources (Resource Manager).** Before running, the job holds a
   worker slot; at completion it takes a rate-limited API slot and a pooled DB
   connection — so no job starves the others.
4. **Run (Consumer).** State → `RUNNING`. The engine simulates the CPU burst
   (`time.sleep`). Round Robin runs only one quantum, decrements remaining time,
   and re-queues if unfinished.
5. **AI + music.** On completion the mock **Gemini** turns the request text into
   attributes (tempo/valence/energy), and the mock **Spotify** returns tracks.
6. **Persist (ACID).** `Database.save_result` writes the `PLAYLIST`, `TRACK`, and
   `PLAYLIST_TRACK` rows and updates `JOB` — all in one transaction that rolls
   back on any error.
7. **Terminate + log.** State → `TERMINATED`; a `JOB_EXECUTION` row records the
   waiting / turnaround / response times for this run's scheduler.

---

## The experiments

`benchmark.py` runs two experiments (results also feed the web **/analytics**
page, backed by `JOB_EXECUTION`):

**1. Classic scheduling (single user).** One workload under FCFS / SJF / Priority
/ Round Robin. Expected: **SJF** minimizes average waiting; **Round Robin** gives
the lowest response time; **FCFS** is the baseline.

**2. Fairness (multi-user).** A FREE "spammer" floods the queue (10 jobs) while a
quiet FREE user and a PREMIUM user submit 2 each, run under FCFS vs FairShare vs
WFQ. Expected, and what it demonstrates:
- **FCFS** — the spammer's jobs run first, so the quiet and (worse) the *paying
  PREMIUM* user wait the longest. Unfair.
- **FairShare** — equal share per user; the spammer can no longer dominate.
- **WFQ** — PREMIUM is served ~3× faster than FREE (its weight), with no
  starvation. This is the "scheduling = business logic" payoff.

Because all jobs are enqueued before the single worker starts, they effectively
"arrive at t=0" and the differences are purely the scheduling policy.

---

## Database

Six normalized tables — `USER`, `JOB`, `JOB_EXECUTION`, `TRACK`, `PLAYLIST`,
`PLAYLIST_TRACK` (`db/schema_sqlite.sql`). `JOB` and `JOB_EXECUTION` carry
`user_id` + `tier` so the fairness analytics can group by user and tier.

Switch to MySQL for submission:

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS wms;"
mysql -u root -p wms < db/schema_mysql.sql
pip install mysql-connector-python
# PowerShell:
$env:WMS_DB_BACKEND="mysql"; $env:WMS_MYSQL_PASSWORD="yourpassword"; python run.py
```

---

## Real providers + export (the 5% + 5%)

Both real providers are already implemented behind `wms/providers/`; put keys in
`.env` and set `WMS_PROVIDERS=auto`:

- **Real Gemini:** free key from https://aistudio.google.com/apikey — works
  immediately once set.
- **Real Spotify:** client-credentials search (no user login). ⚠️ Spotify blocks
  the Web API with 403 unless the **app owner's account has Premium**; until then
  the provider auto-falls-back to mock. The integration is done — it starts
  returning real tracks the moment the owner is Premium.

**Export:** `/playlist/<job_id>` is a shareable page and `/export/<job_id>.m3u`
downloads the playlist.

---

## Tuning (env vars, see `config.py`)

- `WMS_WORKERS` — worker threads / CPUs (default 1, keeps scheduling visible).
- `WMS_QUANTUM_MS` — Round Robin time slice.
- `WMS_API_RATE` — external API requests/minute (rate limiter).
- `WMS_DB_BACKEND` — `sqlite` (default) or `mysql`.

---

## Testing checklist

- [ ] `python benchmark.py` prints the classic table **and** the fairness tables
- [ ] `python run.py` serves the dashboard; submitting a request shows a job move
      NEW → QUEUED → RUNNING → TERMINATED and a playlist appear
- [ ] **Run fairness demo** under FCFS vs WFQ shows PREMIUM served faster under WFQ
- [ ] `/analytics` shows per-scheduler, per-tier, and per-user tables
- [ ] A playlist opens at `/playlist/<id>` and exports via `/export/<id>.m3u`
- [ ] Runs with no API keys and no internet (mock fallback)
