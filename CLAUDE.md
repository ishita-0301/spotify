# CLAUDE.md

Guidance for Claude Code (and humans) working in this repository.

## Project

**Workload Management System — a multi-tenant, fairly-scheduled, AI-powered
music playlist service.** A university OS + DBMS course project (Team
CodingLeague, T-229). A "generate a playlist from a mood" request is modelled as
an operating system **job** owned by a **user** on a service **tier** (FREE /
PREMIUM): created → queued → scheduled → executed by a worker → persisted. The
scheduler decides whose job runs next on a shared, rate-limited API budget, so
scheduling is real business logic (fair sharing, paid tiers) — not just a demo.
Six schedulers: FCFS, SJF, Priority, RoundRobin, FairShare, and WFQ (weighted
fair queuing by tier).

Grading weight: **OS 45%, DBMS 45%, AI 5%, UI 5%.** Keep effort proportional —
the schedulers and the relational/transaction layer are what matter most.

## Tech Stack

- **Language:** Python 3.11+ (standard library threading for the OS layer)
- **Web UI:** Flask (thin; the 5%)
- **Database:** SQLite by default (zero-install, ACID); MySQL supported via
  `WMS_DB_BACKEND=mysql`
- **External services:** mock Gemini + Spotify providers by default (no keys,
  no Spotify Premium). Real APIs are optional.

## Runs with zero setup

`pip install -r requirements.txt` then `python run.py` (or `python benchmark.py`).
Do **not** reintroduce any hard dependency on the real Spotify Web API — it
requires the app owner to have Spotify Premium, which blocked the earlier
approach. Everything must run offline via the mock providers.

## Layout

```
config.py               all tunables (env-overridable) + .env loader
run.py                  start the web app
benchmark.py            classic + fairness comparisons
db/schema_*.sql         relational schema (sqlite + mysql dialects)
.env / .env.example     real provider credentials (gitignored)
wms/
  models.py             Job (+ user_id/tier) + lifecycle + metrics
  users.py              users, tiers, weights (multi-tenant fair scheduling)
  schedulers.py         FCFS, SJF, Priority, RoundRobin, FairShare, WFQ
  job_queue.py          thread-safe ready queue
  resource_manager.py   worker slots, DB pool, API rate limiter
  database.py           connection pool + ACID save_result() + fairness analytics
  engine.py             producer/consumer orchestration (+ notify_run hook)
  providers/            gemini + spotify (real with mock fallback), _http (SSL)
  web/                  Flask app + templates + static
```

## Real providers & the Premium wall

- Real Gemini works with a free API key. Real Spotify search is fully wired but
  **Spotify blocks the Web API entirely (403) unless the app owner's account has
  Premium** — the provider detects this once and falls back to mock for the
  session. Do not try to "fix" the 403 in code; it's a Spotify account policy.
- Keep everything runnable offline: mock fallback must always work.

## Conventions

- Six schedulers, all interchangeable via `select()`; fair schedulers (FairShare,
  WFQ) also implement `notify_run()` to advance per-user virtual time. WFQ needs
  a `weight_fn`; build it via `schedulers.build(name, quantum, weight_fn)`.
- Don't special-case a scheduler in the engine beyond the preemption branch and
  the `notify_run` call.
- All Spotify/Gemini access goes through `wms/providers/` behind the `analyze()`
  / `search()` signatures, so mock and real are swappable.
- Every DB write that spans multiple rows (the playlist + tracks + join + job
  update) must stay inside the single `save_result` transaction.
- SQL uses `?` placeholders; `Database._q` rewrites them for MySQL. Don't inline
  backend-specific SQL in callers.
- Metrics are computed from timestamps on the `Job` (see `models.py`) — waiting
  time must remain correct across Round Robin re-queues.
- Default to 1 worker so scheduling order is observable; don't hardcode more.

## Commands

- `python run.py` — web dashboard at http://127.0.0.1:5000
- `python benchmark.py` — headless scheduler comparison
- `pip install -r requirements.txt` — install deps (Flask; SQLite is built in)

See `BUILD_GUIDE.md` for how the pieces fit together and how to extend it.
