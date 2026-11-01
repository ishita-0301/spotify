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

No API keys or Spotify Premium required — SQLite + mock providers. The web app
does require a (local) account: on first visit you'll be sent to **/login**;
click **Sign up** to create one and pick your tier. `benchmark.py` still needs no
accounts — it seeds its own users headlessly.

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
8. **Offer (opt-in).** After the playlist is saved, the engine generates extra
   genre-matched tracks and stores them as a PENDING `PLAYLIST_SUGGESTION` — the
   saved playlist is **not** mutated. The user later confirms Yes/No (see
   *Genre opt-in suggestions* below).

---

## Accounts & login (the web app)

Every page of the web app is gated behind a login (Flask session cookie signed
with `config.SECRET_KEY` / `WMS_SECRET_KEY`):

- **Sign up** (`/signup`) creates a `USER` row with a tier and a salted password
  hash, then logs you in. **Log in** (`/login`) / **Log out** (`/logout`) manage
  the session. A `login_required` decorator protects the dashboard, analytics,
  submit, demo, benchmark, playlist, export, and the `/api/*` endpoints.
- A job's **owner and tier come from the logged-in account**, not from form
  input — so the person submitting is the person who is scheduled.
- Passwords use **PBKDF2-HMAC-SHA256** with a per-user random salt
  (`wms/auth.py`, standard library only — no new dependencies). Hashes live in
  `USER.password_hash`, stored as `algo$iterations$salt$digest`.
- The fairness demo and `benchmark.py` seed **passwordless** users via
  `Users.get_or_create` (they own jobs but can't log in) — so the experiments
  still run with zero setup.

**Spotify OAuth login (optional, supplemental).** When the `SPOTIFY_OAUTH_*`
credentials are configured, the login/signup pages show a **Log in with
Spotify** button (`wms/spotify_oauth.py`, authorization-code flow):

- `/auth/spotify/login` sends the user to Spotify's consent screen with an
  anti-CSRF `state`; `/auth/spotify/callback` verifies the state, exchanges the
  code for a token, and reads `GET /v1/me`.
- `Users.get_or_create_spotify` maps that profile onto the `USER` schema — linked
  by `spotify_id`, a unique username from the display name, `email` stored — and
  `tier_for_spotify_product` sets the tier (**Spotify Premium → PREMIUM**, else
  FREE), so a Spotify sign-in slots straight into WFQ fair scheduling.
- This is a real alternative login path, not a workaround for the search 403:
  the OAuth + profile endpoints work for any account. With no credentials the
  button is hidden and everything still runs offline.

---

## Live dashboard (auto-update)

Both the dashboard and the analytics page refresh themselves with no reload:

- **Dashboard** polls `/api/jobs` every 1s, so jobs visibly move
  NEW → QUEUED → RUNNING → TERMINATED in real time.
- **Analytics** polls `/api/analytics` every 2s and re-renders the
  per-scheduler, per-tier, and per-user tables as jobs finish or a benchmark
  runs in the background (the server-rendered tables are the initial / no-JS
  fallback).
- Both pages show a `● live · updated Xs ago` indicator that ticks every second
  and resets on each successful fetch, so stale data (e.g. a dropped connection)
  is obvious.

---

## AI recommendations (automatic, per-user)

When a user opens the dashboard, WMS automatically surfaces **personalized
playlist suggestions** — no manual request needed (`wms/recommender.py`):

- **Taste profile.** Because real Spotify account access is blocked by the
  Premium wall, "listening preferences" are derived from the user's **own WMS
  history**: `build_profile` averages the tempo / valence / energy and ranks the
  favorite artists of the tracks in playlists they've generated, and collects
  their recent request moods.
- **Generation.** `generate` synthesizes suggestions from the user's own moods
  first ("More like …"), then from a shuffled set of curated moods, through the
  same **Gemini** (`analyze`) + **Spotify** (`search`) providers the engine uses.
  Each suggestion's target attributes are **blended toward the user's averages**,
  so it reflects their measured taste. Each one carries a title, a plain-language
  **reason**, attribute tags, and a **track preview**.
- **Approval.** Every suggestion is PENDING until the user decides. **Accept**
  calls `Database.accept_recommendation` — a single **ACID transaction** that
  inserts a recommendation-sourced `JOB` (state `TERMINATED`, scheduler
  `RECOMMENDER`), the `PLAYLIST`, its `TRACK` + `PLAYLIST_TRACK` rows, and flips
  the `RECOMMENDATION` to ACCEPTED. **Reject** marks it REJECTED. Nothing is ever
  saved (or written to Spotify) without approval.
- **No repeats.** Each suggestion has a stable `signature`; `generate` skips any
  signature already PENDING or REJECTED, so a dismissed idea never comes back and
  **Refresh** always explores new territory.
- **UI.** The "✨ Recommended for you" panel polls `/api/recommendations` (which
  auto-tops-up to `WMS_REC_COUNT` PENDING suggestions) every 5s; accept / reject
  / refresh hit `POST /api/recommendations/<id>/{accept,reject}` and
  `/refresh`, with ownership (404) and already-decided (409) guards.

---

## Genre opt-in suggestions (confirm before append)

A generated playlist is never silently mutated. After `save_result`, the engine
`_offer_genre_suggestions` searches for extra **genre-matched** tracks (through
the shared rate-limited API slot), drops any already in the playlist, and stores
the rest as a PENDING `PLAYLIST_SUGGESTION` — best-effort, so a failure here
never fails the job. The playlist stays exactly as generated until the user
decides:

- `GET /api/playlist/<job_id>/suggestions` returns the pending offer (ownership
  checked); the `/playlist/<id>` page renders it as an **"Add more … tracks?"**
  panel listing the candidate songs.
- `POST /api/suggestions/<id>/confirm` with `decision=yes` runs
  `Database.accept_playlist_suggestion` — a single **ACID transaction** that
  appends the `TRACK` + `PLAYLIST_TRACK` rows (continuing the existing positions)
  and flips the suggestion to ACCEPTED. `decision=no` marks it REJECTED and
  leaves the playlist untouched. Ownership (404) and already-decided (409) are
  guarded, and every write goes through the existing `_write_lock` + pool.

With the mock Spotify provider the search is seeded by query+genres, so the first
`PLAYLIST_SIZE` results match the saved playlist and are filtered out — the
offered extras are genuinely new, so the opt-in flow demos offline with no keys.

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

Eight normalized tables — `USER`, `JOB`, `JOB_EXECUTION`, `TRACK`, `PLAYLIST`,
`PLAYLIST_TRACK`, `RECOMMENDATION`, and `PLAYLIST_SUGGESTION`
(`db/schema_sqlite.sql`). `JOB` and `JOB_EXECUTION` carry `user_id` + `tier` so
the fairness analytics can group by user and tier. `USER` holds `password_hash`
for login plus `spotify_id` / `email` for Spotify OAuth sign-ins;
`Database.init_schema` migrates older databases by adding any missing columns.
`RECOMMENDATION` holds each AI suggestion's status (PENDING / ACCEPTED /
REJECTED), its preview tracks (as JSON) and a link to the saved `playlist_id`
once accepted. `PLAYLIST_SUGGESTION` holds the genre-matched tracks offered for
opt-in append (same status set, candidate tracks as JSON). New databases pick
the tables up via `CREATE TABLE IF NOT EXISTS`, so no migration is needed.

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
- **Spotify login:** set the `SPOTIFY_OAUTH_*` vars (they default to the client
  id/secret above) and register the redirect URI on the Spotify dashboard to
  enable the **Log in with Spotify** button. The login + profile endpoints work
  for any account — no Premium required (see *Accounts & login* above).

**Export:** `/playlist/<job_id>` is a shareable page and `/export/<job_id>.m3u`
downloads the playlist.

---

## Tuning (env vars, see `config.py`)

- `WMS_WORKERS` — worker threads / CPUs (default 1, keeps scheduling visible).
- `WMS_QUANTUM_MS` — Round Robin time slice.
- `WMS_API_RATE` — external API requests/minute (rate limiter).
- `WMS_DB_BACKEND` — `sqlite` (default) or `mysql`.
- `WMS_REC_COUNT` — PENDING recommendations kept in front of each user (default 3).
- `WMS_REC_PREVIEW` — preview tracks shown per recommendation (default 5).
- `WMS_GENRE_SUGGESTIONS` — genre-matched tracks offered for opt-in append (default 5).
- `SPOTIFY_OAUTH_*` — enable the optional "Log in with Spotify" button.

---

## Testing checklist

- [ ] `python benchmark.py` prints the classic table **and** the fairness tables
- [ ] `python run.py` redirects to **/login**; **Sign up** creates an account and
      logs in; **Log out** returns to the login page
- [ ] Visiting any page while logged out redirects to login; a wrong password is
      rejected
- [ ] Submitting a request shows a job move NEW → QUEUED → RUNNING → TERMINATED
      and a playlist appear, owned by the logged-in account
- [ ] The dashboard and `/analytics` update live (no reload) with a
      `● live · updated Xs ago` indicator
- [ ] **Run fairness demo** under FCFS vs WFQ shows PREMIUM served faster under WFQ
- [ ] `/analytics` shows per-scheduler, per-tier, and per-user tables
- [ ] A playlist opens at `/playlist/<id>` and exports via `/export/<id>.m3u`
- [ ] The dashboard shows **✨ Recommended for you** suggestions automatically on
      login, each with a title, reason, attribute tags, and a track preview
- [ ] **Accept** saves the playlist (it appears in the jobs table and at
      `/playlist/<id>`); **Reject** dismisses it and it never reappears, even after
      **Refresh**; another user can't accept/reject yours
- [ ] On `/playlist/<id>` the **"Add more … tracks?"** panel lists genre-matched
      songs not yet in the playlist; **Yes** appends them (playlist grows), **No**
      leaves it unchanged; a decided suggestion can't be re-decided (409)
- [ ] With `SPOTIFY_OAUTH_*` set, **Log in with Spotify** signs in and maps the
      account (Premium → PREMIUM tier); with them unset, no button is shown
- [ ] Runs with no API keys and no internet (mock fallback)
