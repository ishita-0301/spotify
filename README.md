# 🎧 Workload Management System

A **multi-tenant, fairly-scheduled, AI-powered music playlist service** —
a university OS + DBMS course project (Team CodingLeague, T-229).

A "generate a playlist from a mood" request is modelled as an operating-system
**job** owned by a **user** on a service **tier** (FREE / PREMIUM): created →
queued → scheduled → executed by a worker → persisted. A shared, rate-limited API
budget means the **scheduler decides whose job runs next** — so scheduling is
real business logic (fair sharing, paid tiers), not just a demo.

> **Runs with zero setup.** SQLite + mock Gemini/Spotify providers by default —
> no API keys, no Spotify Premium, no internet required. Real providers are
> optional.

---

## Quick start

```bash
pip install -r requirements.txt
python run.py          # web dashboard at http://127.0.0.1:5000
# or
python benchmark.py    # headless scheduler comparison
```

On first visit you'll be sent to **/login** — click **Sign up**, pick a tier, and
you're in. `benchmark.py` seeds its own users and needs no login.

---

## Features

- **Six schedulers**, interchangeable at runtime: FCFS, SJF, Priority, Round
  Robin, FairShare, and **WFQ** (weighted fair queuing — PREMIUM served ~3×
  FREE, no starvation).
- **ACID persistence** — each playlist (PLAYLIST + TRACK + PLAYLIST_TRACK + JOB
  update) is written in a single all-or-nothing transaction, over SQLite or
  MySQL.
- **Multi-user auth** — every page gated behind login; salted PBKDF2 password
  hashes (standard library only). A job's owner/tier come from the account.
- **Live dashboard** — jobs move NEW → QUEUED → RUNNING → TERMINATED in real
  time; `/analytics` shows fairness per scheduler / tier / user.
- **✨ AI playlist recommendations** — on login the dashboard automatically
  surfaces personalized suggestions built from your taste (tempo / energy /
  valence / favorite artists), each with a reason and a track preview. **Accept**
  (saved to your account), **Reject** (never shown again), or **Refresh** for
  more. Nothing is saved without your approval.
- **Export** — any playlist is shareable at `/playlist/<id>` and downloadable as
  `.m3u`.

---

## Layout

```
config.py               all tunables (env-overridable) + .env loader
run.py                  start the web app
benchmark.py            classic + fairness comparisons
db/schema_*.sql         relational schema (sqlite + mysql dialects)
wms/
  models.py             Job + lifecycle + scheduling metrics
  auth.py               stdlib PBKDF2 password hashing
  users.py              users, tiers, weights, register/authenticate
  recommender.py        AI playlist recommendations from a per-user taste profile
  schedulers.py         FCFS, SJF, Priority, RoundRobin, FairShare, WFQ
  job_queue.py          thread-safe ready queue
  resource_manager.py   worker slots, DB pool, API rate limiter
  database.py           connection pool + ACID transactions + analytics
  engine.py             producer/consumer orchestration
  providers/            gemini + spotify (real with mock fallback)
  web/                  Flask app + templates + static
```

Seven normalized tables: `USER`, `JOB`, `JOB_EXECUTION`, `TRACK`, `PLAYLIST`,
`PLAYLIST_TRACK`, `RECOMMENDATION`.

---

## Configuration

Everything has a zero-setup default; override via environment variables (see
`config.py`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `WMS_DB_BACKEND` | `sqlite` | `sqlite` or `mysql` |
| `WMS_WORKERS` | `1` | worker threads (1 keeps scheduling order visible) |
| `WMS_QUANTUM_MS` | `150` | Round Robin time slice |
| `WMS_API_RATE` | `240` | external API requests / minute |
| `WMS_PROVIDERS` | `auto` | `auto` / `mock` / `real` |
| `WMS_REC_COUNT` | `3` | PENDING recommendations kept per user |
| `WMS_REC_PREVIEW` | `5` | preview tracks per recommendation |
| `WMS_SECRET_KEY` | dev value | signs the session cookie — set for non-local use |

**Real providers** (optional): put `GEMINI_API_KEY` (free) and/or
`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` in a `.env` file. Spotify search is
fully wired but the Web API returns 403 unless the app owner's account is
Premium — the provider detects this once and falls back to mock for the session.

**MySQL:**

```bash
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS wms;"
mysql -u root -p wms < db/schema_mysql.sql
pip install mysql-connector-python
# PowerShell:
$env:WMS_DB_BACKEND="mysql"; $env:WMS_MYSQL_PASSWORD="yourpassword"; python run.py
```

---

## Documentation

- **`BUILD_GUIDE.md`** — how the pieces fit together, the job lifecycle, the
  experiments, and how to extend it.
- **`CLAUDE.md`** — project conventions and constraints.
- **`Phase2.md`** — login & live dashboard notes. **`Phase3.md`** — AI
  recommendations notes.

Grading weight: **OS 45%, DBMS 45%, AI 5%, UI 5%** — the schedulers and the
relational/transaction layer are what matter most.
