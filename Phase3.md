# Phase 3 — AI-Based Automatic Playlist Recommendations

Changes and updates made after the login & live-dashboard phase (Team
CodingLeague, T-229).

This phase adds a **personalized recommendation system**: whenever a user opens
the dashboard, WMS automatically generates playlist suggestions from their own
taste, each with an explanation and a track preview, and lets them **accept** or
**reject** with a single click. Nothing is ever saved (or written to Spotify)
without the user's approval, and rejected suggestions are never shown again.

This maps the brief's "connected Spotify account" to what the project can do
offline: a user's taste profile is built from **their own WMS history** — the
moods they've requested and the audio attributes (tempo / energy / valence) and
artists of the tracks in playlists they've already generated. Real Spotify
account access stays blocked by the Premium wall (see `CLAUDE.md`), so the mock
providers remain the source of truth and everything runs with zero setup.

---

## 1. Recommendation engine

**New file**

- `wms/recommender.py`
  - `build_profile(db, user_id)` — averages tempo / valence / energy and ranks
    favorite artists across the user's generated tracks; also collects their
    recent request moods.
  - `generate(db, user, count)` — synthesizes new suggestions from the user's
    own moods first ("More like …"), then from a shuffled set of curated moods,
    via the existing `gemini.analyze()` + `spotify.search()` providers. Each
    suggestion's target attributes are **blended toward the user's averages**, and
    every suggestion gets a title, a plain-language reason, and a track preview.
  - `ensure(db, user, target)` — tops the user's PENDING suggestions up to
    `RECOMMENDATION_COUNT`; called on every dashboard load so recommendations
    appear automatically.
  - A suggestion's stable `signature` is what prevents re-showing a rejected (or
    duplicating a pending) idea.

## 2. Database — RECOMMENDATION table + ACID accept

**Changed files**

- `db/schema_sqlite.sql`, `db/schema_mysql.sql` — new `RECOMMENDATION` table
  (`status` = PENDING | ACCEPTED | REJECTED, preview tracks stored as
  `tracks_json`, link to the saved `playlist_id` once accepted) + an index on
  `(user_id, status)`. `CREATE TABLE IF NOT EXISTS` means existing `wms.db`
  files pick it up on next start with no migration.
- `wms/database.py`
  - Taste reads: `recent_requests`, `taste_tracks`.
  - CRUD: `insert_recommendation`, `list_recommendations`, `get_recommendation`,
    `count_recommendations`, `recommendation_signatures`, `reject_recommendation`.
  - `accept_recommendation` — a single **ACID transaction** that inserts a
    recommendation-sourced JOB (state `TERMINATED`, scheduler `RECOMMENDER`), the
    PLAYLIST, its TRACK + PLAYLIST_TRACK rows, and flips the RECOMMENDATION to
    ACCEPTED — all-or-nothing, matching the `save_result` convention.

## 3. Web UI

**Changed files**

- `wms/web/app.py`
  - `GET /api/recommendations` — auto-tops-up and returns the user's PENDING
    suggestions (tracks parsed from JSON).
  - `POST /api/recommendations/refresh` — request fresh suggestions.
  - `POST /api/recommendations/<id>/accept` — save it (ownership + PENDING
    checked; 404 for someone else's, 409 if already decided).
  - `POST /api/recommendations/<id>/reject` — dismiss it.
- `wms/web/templates/index.html` — a "✨ Recommended for you" panel that renders
  cards (title, reason, attribute tags, track preview, Accept / Reject) and polls
  `/api/recommendations` every 5s, plus a Refresh button. Accepting also refreshes
  the jobs table so the newly saved playlist shows up immediately.
- `wms/web/static/style.css` — recommendation card grid + card styles.

## 4. Config

- `config.py` — `RECOMMENDATION_COUNT` (`WMS_REC_COUNT`, default 3) and
  `RECOMMENDATION_PREVIEW` (`WMS_REC_PREVIEW`, default 5).

---

## How to verify

```bash
python run.py          # log in; the dashboard shows suggestions automatically
```

Checklist:

- [ ] On login the dashboard shows personalized recommendations with no manual
      request; each has a title, reason, attribute tags, and a track preview.
- [ ] Accept saves a playlist (visible in the jobs table and `/playlist/<id>`)
      and marks the recommendation ACCEPTED; Reject dismisses it.
- [ ] A rejected suggestion never reappears, even after Refresh.
- [ ] Refresh requests new suggestions; another user can't accept/reject yours.
- [ ] Runs offline with mock providers and no API keys.
