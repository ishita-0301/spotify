# Phase 4 — Spotify OAuth Login & Genre Opt-in Suggestions

Changes and updates made after the AI-recommendations phase (Team
CodingLeague, T-229).

This phase adds two mentor-requested features, both of which **preserve the
existing OS/DBMS core** — the thread-safe ready queue, the six schedulers
(incl. WFQ), the Resource Manager semaphores, and the pooled/ACID database layer
are untouched:

1. **Spotify OAuth login** — an optional, supplemental sign-in so a user can log
   in with their Spotify account instead of creating a local password account.
2. **Genre-based opt-in suggestions** — after a playlist is generated, extra
   genre-matched tracks are *offered* (never auto-added); the user confirms
   **Yes** (append) or **No** (keep the playlist exactly as generated).

Everything still runs offline with zero setup: the Spotify button only appears
when OAuth credentials are configured, and the genre suggestions work with the
mock Spotify provider.

---

## 1. Spotify OAuth login (optional, supplemental auth)

**New file**

- `wms/spotify_oauth.py` — the OAuth 2.0 **authorization-code** flow:
  - `configured()` — True only when client id/secret/redirect are set (decides
    whether the "Log in with Spotify" button is shown at all).
  - `new_state()` / `authorize_url(state)` — anti-CSRF state + the consent URL.
  - `exchange_code(code)` — trades the code for an access token.
  - `fetch_profile(token)` — reads `GET /v1/me` → `{spotify_id, display_name,
    email, product}`.
  - All HTTP reuses the shared SSL-aware `providers._http.urlopen`.

> Unlike catalog **search** (blocked by the Premium wall — see `CLAUDE.md`), the
> authorize + profile endpoints work for any registered Spotify account, so this
> is a genuine alternative login path — not a workaround for the 403.

**Changed files**

- `config.py` — `SPOTIFY_OAUTH_CLIENT_ID` / `SPOTIFY_OAUTH_CLIENT_SECRET`
  (default to the existing `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`, so one
  Spotify app covers both search and login), `SPOTIFY_OAUTH_REDIRECT_URI`,
  `SPOTIFY_OAUTH_SCOPES` (`user-read-email user-read-private`).
- `wms/users.py`
  - `tier_for_spotify_product(product)` — maps a **Spotify Premium** account to
    the **PREMIUM** tier (weight 3.0) and everyone else to **FREE** (weight 1.0),
    so Spotify sign-ins slot straight into WFQ fair scheduling.
  - `get_or_create_spotify(spotify_id, display_name, email, tier)` — finds (by
    `spotify_id`) or creates the local `USER`, generates a collision-free
    username from the display name, and keeps the tier in sync on re-login.
    Backed by a new in-memory `_by_spotify` index (thread-safe under the
    existing `Users._lock`).
- `wms/database.py`
  - `USER` now carries `spotify_id` and `email`; `insert_user` / `list_users`
    were extended to read/write them.
  - `_ensure_user_columns` (renamed from `_ensure_password_column`) idempotently
    adds `password_hash`, `spotify_id`, and `email` to pre-existing databases —
    so old `wms.db` files migrate with no manual step.
- `wms/web/app.py`
  - `GET /auth/spotify/login` — starts the flow (503 render if not configured).
  - `GET /auth/spotify/callback` — verifies `state`, exchanges the code, maps the
    profile onto a local account via `get_or_create_spotify`, and logs the user
    in. Network/Spotify errors render gracefully on the login page.
  - A context processor exposes `spotify_login_enabled` to every template.
- `wms/web/templates/login.html`, `signup.html` — a green **Log in with
  Spotify** button, shown only when `spotify_login_enabled`.
- `wms/web/static/style.css` — `a.button` / `a.button.spotify` styles.
- `.env.example` — documents the (optional) `SPOTIFY_OAUTH_*` variables.

## 2. Genre-based opt-in suggestions (no auto-mutation)

Previously a generated playlist was the final artifact. Now, after it is saved,
the system **offers** extra genre-matched tracks for the user to append — the
playlist is never changed until the user explicitly confirms.

**New table**

- `PLAYLIST_SUGGESTION` (both SQL dialects) — `status` = PENDING | ACCEPTED |
  REJECTED, candidate tracks stored as `tracks_json`, linked to `playlist_id` /
  `job_id` / `user_id`, with an index on `(playlist_id, status)`. New databases
  pick it up via `CREATE TABLE IF NOT EXISTS` — no migration.

**Changed files**

- `wms/engine.py` — `_complete` now calls `_offer_genre_suggestions(job, attrs,
  tracks)` **after** `save_result`. It searches for `PLAYLIST_SIZE +
  GENRE_SUGGESTION_COUNT` genre-matched tracks, drops any already in the
  playlist, and stores the extras as a **PENDING** suggestion. It goes through
  the shared **rate-limited** API slot and is best-effort — any failure is logged
  and never fails the job. **The saved playlist is not mutated here.**
- `wms/database.py`
  - `insert_playlist_suggestion`, `get_playlist_suggestion`,
    `pending_suggestion_for_playlist`, `reject_playlist_suggestion`.
  - `accept_playlist_suggestion` — a single **ACID transaction** that appends the
    suggested `TRACK` rows and `PLAYLIST_TRACK` join rows (continuing the
    existing positions) and flips the suggestion to ACCEPTED — all-or-nothing,
    matching the `save_result` convention. All writes use the existing
    `_write_lock` + connection pool.
- `wms/web/app.py`
  - `GET /api/playlist/<job_id>/suggestions` — returns the PENDING offer for the
    playlist (ownership-checked), or `{"suggestion": null}`.
  - `POST /api/suggestions/<id>/confirm` — `decision=yes` appends (ACID),
    `decision=no` keeps the playlist unchanged. Ownership (404) and
    already-decided (409) guards; accepts form, query, or JSON body.
- `wms/web/templates/playlist.html` — an opt-in panel that fetches the pending
  suggestion, lists the candidate tracks, and offers **Yes, append** / **No,
  keep as is** (reloading on accept to show the longer playlist).
- `config.py` — `GENRE_SUGGESTION_COUNT` (`WMS_GENRE_SUGGESTIONS`, default 5).

> With the **mock** Spotify provider the search is seeded by query+genres, so the
> first `PLAYLIST_SIZE` results match the saved playlist exactly and are filtered
> out — leaving genuinely new extras. The opt-in demo therefore works offline
> with no keys.

---

## What was deliberately NOT changed

Per the brief, the OS/DBMS core was preserved: `job_queue.ReadyQueue`, all six
schedulers and WFQ virtual-time, `resource_manager` semaphores / rate limiter,
and the DB connection pool + `_write_lock` transaction discipline. Both features
are additive and route every write through the existing ACID/locking paths.

---

## How to verify

```bash
python run.py
```

Checklist:

- [ ] With no Spotify OAuth credentials, the login/signup pages show **no**
      Spotify button and the app runs normally on mocks.
- [ ] With credentials set (and the redirect URI registered on the Spotify
      dashboard), **Log in with Spotify** signs in; a Premium account lands on the
      PREMIUM tier, a free account on FREE; re-login reuses the same local user.
- [ ] After generating a playlist, `/playlist/<id>` shows a **"Add more … tracks?"**
      panel listing genre-matched songs that are **not** yet in the playlist.
- [ ] **Yes** appends them (the playlist grows, positions continue); **No** leaves
      the playlist exactly as generated. A decided suggestion can't be decided
      again (409); another user can't act on yours (404).
- [ ] Runs offline with mock providers and no API keys.
