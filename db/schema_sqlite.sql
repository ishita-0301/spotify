-- Workload Management System — relational schema (SQLite dialect)
-- Tables: USER, JOB, JOB_EXECUTION, TRACK, PLAYLIST, PLAYLIST_TRACK

CREATE TABLE IF NOT EXISTS USER (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    tier          TEXT NOT NULL DEFAULT 'FREE',   -- FREE | PREMIUM
    password_hash TEXT,                           -- NULL = seeded / Spotify-only login
    spotify_id    TEXT,                           -- set when the account signed in via Spotify OAuth
    email         TEXT,                           -- optional, from the Spotify profile
    created_at    REAL
);

CREATE INDEX IF NOT EXISTS idx_user_spotify ON USER(spotify_id);

CREATE TABLE IF NOT EXISTS JOB (
    job_id        INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    owner         TEXT    NOT NULL,             -- denormalized name for display
    tier          TEXT    NOT NULL DEFAULT 'FREE',
    request_text  TEXT    NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 5,   -- lower value = higher priority
    burst_ms      INTEGER NOT NULL,             -- estimated service time
    state         TEXT    NOT NULL,             -- NEW | QUEUED | RUNNING | TERMINATED
    scheduler     TEXT,
    created_at    REAL,
    enqueued_at   REAL,
    started_at    REAL,
    finished_at   REAL,
    FOREIGN KEY (user_id) REFERENCES USER(user_id)
);

CREATE TABLE IF NOT EXISTS JOB_EXECUTION (
    exec_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    tier          TEXT    NOT NULL,
    scheduler     TEXT    NOT NULL,
    waiting_ms    REAL    NOT NULL,
    turnaround_ms REAL    NOT NULL,
    response_ms   REAL    NOT NULL,
    status        TEXT    NOT NULL,
    started_at    REAL,
    finished_at   REAL,
    FOREIGN KEY (job_id)  REFERENCES JOB(job_id),
    FOREIGN KEY (user_id) REFERENCES USER(user_id)
);

CREATE TABLE IF NOT EXISTS TRACK (
    track_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name     TEXT NOT NULL,
    artist   TEXT NOT NULL,
    spotify_url TEXT,
    tempo    REAL,
    valence  REAL,
    energy   REAL
);

CREATE TABLE IF NOT EXISTS PLAYLIST (
    playlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    created_at  REAL,
    FOREIGN KEY (job_id) REFERENCES JOB(job_id)
);

CREATE TABLE IF NOT EXISTS PLAYLIST_TRACK (
    playlist_id INTEGER NOT NULL,
    track_id    INTEGER NOT NULL,
    position    INTEGER NOT NULL,
    PRIMARY KEY (playlist_id, track_id),
    FOREIGN KEY (playlist_id) REFERENCES PLAYLIST(playlist_id),
    FOREIGN KEY (track_id)    REFERENCES TRACK(track_id)
);

-- AI-generated playlist recommendations awaiting the user's accept/reject.
-- tracks_json holds the previewed tracks; on accept they become a real PLAYLIST.
CREATE TABLE IF NOT EXISTS RECOMMENDATION (
    rec_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    title        TEXT    NOT NULL,
    reason       TEXT    NOT NULL,          -- why it was recommended
    signature    TEXT    NOT NULL,          -- stable key: never re-show a rejected one
    seed_text    TEXT    NOT NULL,          -- the mood used to generate it
    genres       TEXT,                      -- comma-joined target genres
    tempo        REAL,
    valence      REAL,
    energy       REAL,
    tracks_json  TEXT    NOT NULL,          -- preview tracks as JSON
    status       TEXT    NOT NULL DEFAULT 'PENDING',  -- PENDING | ACCEPTED | REJECTED
    playlist_id  INTEGER,                   -- set once accepted
    created_at   REAL,
    decided_at   REAL,
    FOREIGN KEY (user_id)     REFERENCES USER(user_id),
    FOREIGN KEY (playlist_id) REFERENCES PLAYLIST(playlist_id)
);

-- Genre-matched extra tracks offered after a playlist is generated. The
-- playlist is NOT mutated until the user confirms ("Yes" -> append the tracks,
-- "No" -> keep the playlist as originally generated). tracks_json holds the
-- candidate tracks; on accept they are appended to PLAYLIST in one transaction.
CREATE TABLE IF NOT EXISTS PLAYLIST_SUGGESTION (
    suggestion_id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id   INTEGER NOT NULL,
    job_id        INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    genres        TEXT,                      -- comma-joined genres these match
    tracks_json   TEXT    NOT NULL,          -- candidate tracks as JSON
    status        TEXT    NOT NULL DEFAULT 'PENDING',  -- PENDING | ACCEPTED | REJECTED
    created_at    REAL,
    decided_at    REAL,
    FOREIGN KEY (playlist_id) REFERENCES PLAYLIST(playlist_id),
    FOREIGN KEY (job_id)      REFERENCES JOB(job_id),
    FOREIGN KEY (user_id)     REFERENCES USER(user_id)
);

CREATE INDEX IF NOT EXISTS idx_sug_playlist ON PLAYLIST_SUGGESTION(playlist_id, status);

CREATE INDEX IF NOT EXISTS idx_exec_scheduler ON JOB_EXECUTION(scheduler);
CREATE INDEX IF NOT EXISTS idx_exec_tier      ON JOB_EXECUTION(tier);
CREATE INDEX IF NOT EXISTS idx_job_state      ON JOB(state);
CREATE INDEX IF NOT EXISTS idx_rec_user       ON RECOMMENDATION(user_id, status);
