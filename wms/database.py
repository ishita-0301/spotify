"""The DBMS layer.

Owns a small pool of connections and exposes typed operations over the five
normalized tables. The key DBMS concept on display is the ACID transaction in
`save_result`: a playlist, its tracks, and the join rows are all written (and
the JOB row updated) atomically — either everything commits or everything rolls
back. Works against SQLite (default) or MySQL via a paramstyle swap.
"""
from __future__ import annotations

import json
import os
import queue
import threading
from typing import List, Optional

import config
from .models import Job, next_job_id, now_ms

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Database:
    def __init__(self):
        self.backend = config.DB_BACKEND
        # SQLite serializes writers, so we guard writes with a single lock to
        # avoid "database is locked" under concurrent workers.
        self._write_lock = threading.Lock()
        self._pool: "queue.Queue" = queue.Queue()

        if self.backend == "sqlite":
            import sqlite3
            self.ph = "?"                       # parameter placeholder
            self._sqlite3 = sqlite3
            for _ in range(config.DB_POOL_SIZE):
                conn = sqlite3.connect(
                    config.SQLITE_PATH, check_same_thread=False, timeout=30
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                self._pool.put(conn)
        elif self.backend == "mysql":
            import mysql.connector
            self.ph = "%s"
            for _ in range(config.DB_POOL_SIZE):
                self._pool.put(mysql.connector.connect(**config.MYSQL))
        else:
            raise ValueError(f"Unknown DB backend: {self.backend!r}")

    # ---- connection pool -------------------------------------------------
    def get_conn(self):
        return self._pool.get()

    def put_conn(self, conn) -> None:
        self._pool.put(conn)

    def _q(self, sql: str) -> str:
        """Swap the '?' placeholders in our SQL for the backend's style."""
        return sql if self.ph == "?" else sql.replace("?", self.ph)

    # ---- schema ----------------------------------------------------------
    def init_schema(self) -> None:
        fname = "schema_sqlite.sql" if self.backend == "sqlite" else "schema_mysql.sql"
        with open(os.path.join(_HERE, "db", fname), "r", encoding="utf-8") as f:
            sql = f.read()
        conn = self.get_conn()
        try:
            if self.backend == "sqlite":
                conn.executescript(sql)
            else:
                cur = conn.cursor()
                for stmt in [s for s in sql.split(";") if s.strip()]:
                    cur.execute(stmt)
                cur.close()
            self._ensure_password_column(conn)
            conn.commit()
        finally:
            self.put_conn(conn)

    def _ensure_password_column(self, conn) -> None:
        """Idempotently add USER.password_hash to databases created before auth
        was introduced (CREATE TABLE IF NOT EXISTS won't alter an existing one)."""
        if self.backend == "sqlite":
            cols = [r[1] for r in conn.execute("PRAGMA table_info(USER)").fetchall()]
            if "password_hash" not in cols:
                conn.execute("ALTER TABLE USER ADD COLUMN password_hash TEXT")
        else:
            try:
                cur = conn.cursor()
                cur.execute("ALTER TABLE USER ADD COLUMN password_hash VARCHAR(255)")
                cur.close()
            except Exception:
                pass  # column already exists

    # ---- USER writes -----------------------------------------------------
    def list_users(self) -> List[dict]:
        return [dict(r) for r in self._query(
            "SELECT user_id, name, tier, password_hash FROM USER")]

    def insert_user(self, name: str, tier: str, created_at: float,
                    password_hash: Optional[str] = None) -> int:
        with self._write_lock:
            conn = self.get_conn()
            try:
                cur = conn.cursor()
                cur.execute(self._q(
                    "INSERT INTO USER (name, tier, password_hash, created_at) "
                    "VALUES (?, ?, ?, ?)"),
                    (name, tier, password_hash, created_at))
                user_id = cur.lastrowid
                conn.commit()
                return user_id
            finally:
                self.put_conn(conn)

    def update_user_tier(self, user_id: int, tier: str) -> None:
        with self._write_lock:
            conn = self.get_conn()
            try:
                self._exec(conn, self._q(
                    "UPDATE USER SET tier=? WHERE user_id=?"), (tier, user_id))
                conn.commit()
            finally:
                self.put_conn(conn)

    # ---- JOB writes ------------------------------------------------------
    def insert_job(self, job: Job) -> None:
        sql = self._q(
            "INSERT INTO JOB (job_id, user_id, owner, tier, request_text, "
            "priority, burst_ms, state, scheduler, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            job.job_id, job.user_id, job.owner, job.tier, job.request_text,
            job.priority, job.burst_ms, job.state.value, job.scheduler_name,
            job.created_at,
        )
        with self._write_lock:
            conn = self.get_conn()
            try:
                self._exec(conn, sql, params)
                conn.commit()
            finally:
                self.put_conn(conn)

    def update_job_state(self, job: Job) -> None:
        sql = self._q(
            "UPDATE JOB SET state=?, scheduler=?, enqueued_at=?, started_at=?, "
            "finished_at=? WHERE job_id=?"
        )
        params = (
            job.state.value, job.scheduler_name, job.first_enqueued_at,
            job.started_at, job.finished_at, job.job_id,
        )
        with self._write_lock:
            conn = self.get_conn()
            try:
                self._exec(conn, sql, params)
                conn.commit()
            finally:
                self.put_conn(conn)

    # ---- the ACID transaction -------------------------------------------
    def save_result(self, job: Job, title: str, tracks: List[dict]) -> int:
        """Atomically persist the generated playlist and mark the JOB finished.

        Inserts TRACK rows, one PLAYLIST row, the PLAYLIST_TRACK join rows, and
        updates JOB — all in a single transaction. Any error rolls the whole
        thing back so we never leave a half-written playlist.
        """
        with self._write_lock:
            conn = self.get_conn()
            try:
                cur = conn.cursor()
                # PLAYLIST
                cur.execute(self._q(
                    "INSERT INTO PLAYLIST (job_id, title, created_at) VALUES (?, ?, ?)"
                ), (job.job_id, title, job.finished_at))
                playlist_id = cur.lastrowid

                # TRACK + PLAYLIST_TRACK
                for pos, t in enumerate(tracks, start=1):
                    cur.execute(self._q(
                        "INSERT INTO TRACK (name, artist, spotify_url, tempo, "
                        "valence, energy) VALUES (?, ?, ?, ?, ?, ?)"
                    ), (t["name"], t["artist"], t.get("spotify_url"),
                        t["tempo"], t["valence"], t["energy"]))
                    track_id = cur.lastrowid
                    cur.execute(self._q(
                        "INSERT INTO PLAYLIST_TRACK (playlist_id, track_id, position) "
                        "VALUES (?, ?, ?)"
                    ), (playlist_id, track_id, pos))

                # JOB — mark terminated
                cur.execute(self._q(
                    "UPDATE JOB SET state=?, scheduler=?, started_at=?, finished_at=? "
                    "WHERE job_id=?"
                ), (job.state.value, job.scheduler_name, job.started_at,
                    job.finished_at, job.job_id))

                conn.commit()
                return playlist_id
            except Exception:
                conn.rollback()
                raise
            finally:
                self.put_conn(conn)

    def insert_execution(self, job: Job) -> None:
        sql = self._q(
            "INSERT INTO JOB_EXECUTION (job_id, user_id, tier, scheduler, "
            "waiting_ms, turnaround_ms, response_ms, status, started_at, "
            "finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            job.job_id, job.user_id, job.tier, job.scheduler_name,
            job.waiting_ms, job.turnaround_ms, job.response_ms, "COMPLETED",
            job.started_at, job.finished_at,
        )
        with self._write_lock:
            conn = self.get_conn()
            try:
                self._exec(conn, sql, params)
                conn.commit()
            finally:
                self.put_conn(conn)

    # ---- reads -----------------------------------------------------------
    def list_jobs(self, limit: int = 100) -> List[dict]:
        rows = self._query(
            "SELECT job_id, user_id, owner, tier, request_text, priority, "
            "burst_ms, state, scheduler, created_at FROM JOB "
            "ORDER BY job_id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def playlist_for_job(self, job_id: int) -> Optional[dict]:
        pl = self._query(self._q(
            "SELECT playlist_id, title FROM PLAYLIST WHERE job_id=? "
            "ORDER BY playlist_id DESC LIMIT 1"), (job_id,))
        if not pl:
            return None
        pl = dict(pl[0])
        tracks = self._query(self._q(
            "SELECT t.name, t.artist, t.spotify_url, t.tempo, t.valence, "
            "t.energy, pt.position FROM PLAYLIST_TRACK pt "
            "JOIN TRACK t ON t.track_id = pt.track_id "
            "WHERE pt.playlist_id=? ORDER BY pt.position"), (pl["playlist_id"],))
        pl["tracks"] = [dict(t) for t in tracks]
        return pl

    def max_job_id(self) -> int:
        rows = self._query("SELECT MAX(job_id) AS m FROM JOB")
        m = rows[0]["m"] if rows else None
        return int(m) if m else 0

    def analytics(self) -> List[dict]:
        rows = self._query(
            "SELECT scheduler, COUNT(*) AS n, AVG(waiting_ms) AS avg_waiting, "
            "AVG(turnaround_ms) AS avg_turnaround, AVG(response_ms) AS avg_response "
            "FROM JOB_EXECUTION GROUP BY scheduler ORDER BY scheduler"
        )
        return [dict(r) for r in rows]

    def analytics_by_tier(self) -> List[dict]:
        """Fairness view: how each service tier is served, per scheduler."""
        rows = self._query(
            "SELECT scheduler, tier, COUNT(*) AS n, AVG(waiting_ms) AS avg_waiting, "
            "AVG(turnaround_ms) AS avg_turnaround, AVG(response_ms) AS avg_response "
            "FROM JOB_EXECUTION GROUP BY scheduler, tier ORDER BY scheduler, tier"
        )
        return [dict(r) for r in rows]

    def analytics_by_user(self) -> List[dict]:
        """Fairness view: per-user service, grouped by scheduler."""
        rows = self._query(
            "SELECT e.user_id, u.name, e.tier, e.scheduler, COUNT(*) AS n, "
            "AVG(e.waiting_ms) AS avg_waiting "
            "FROM JOB_EXECUTION e JOIN USER u ON u.user_id = e.user_id "
            "GROUP BY e.user_id, e.scheduler ORDER BY e.scheduler, avg_waiting DESC"
        )
        return [dict(r) for r in rows]

    def clear_executions(self) -> None:
        with self._write_lock:
            conn = self.get_conn()
            try:
                self._exec(conn, "DELETE FROM JOB_EXECUTION", ())
                conn.commit()
            finally:
                self.put_conn(conn)

    # ---- taste profile (for AI recommendations) --------------------------
    def recent_requests(self, user_id: int, limit: int = 20) -> List[str]:
        """The user's most recent playlist requests — a proxy for their taste."""
        rows = self._query(
            "SELECT request_text FROM JOB WHERE user_id=? "
            "ORDER BY job_id DESC LIMIT ?", (user_id, limit))
        return [r["request_text"] for r in rows]

    def taste_tracks(self, user_id: int, limit: int = 100) -> List[dict]:
        """Attributes + artists of the tracks in playlists this user generated."""
        rows = self._query(
            "SELECT t.artist, t.tempo, t.valence, t.energy FROM TRACK t "
            "JOIN PLAYLIST_TRACK pt ON pt.track_id = t.track_id "
            "JOIN PLAYLIST p ON p.playlist_id = pt.playlist_id "
            "JOIN JOB j ON j.job_id = p.job_id "
            "WHERE j.user_id=? ORDER BY t.track_id DESC LIMIT ?",
            (user_id, limit))
        return [dict(r) for r in rows]

    # ---- RECOMMENDATION --------------------------------------------------
    def insert_recommendation(self, user_id: int, title: str, reason: str,
                              signature: str, seed_text: str, attrs: dict,
                              tracks: List[dict]) -> int:
        genres = ",".join(attrs.get("genres", []))
        sql = self._q(
            "INSERT INTO RECOMMENDATION (user_id, title, reason, signature, "
            "seed_text, genres, tempo, valence, energy, tracks_json, status, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)"
        )
        params = (user_id, title, reason, signature, seed_text, genres,
                  attrs.get("tempo"), attrs.get("valence"), attrs.get("energy"),
                  json.dumps(tracks), now_ms())
        with self._write_lock:
            conn = self.get_conn()
            try:
                cur = conn.cursor()
                cur.execute(sql, params)
                rec_id = cur.lastrowid
                conn.commit()
                return rec_id
            finally:
                self.put_conn(conn)

    _REC_COLS = ("rec_id, user_id, title, reason, signature, seed_text, genres, "
                 "tempo, valence, energy, tracks_json, status, playlist_id, "
                 "created_at, decided_at")

    def list_recommendations(self, user_id: int, status: str = "PENDING",
                             limit: int = 20) -> List[dict]:
        rows = self._query(
            f"SELECT {self._REC_COLS} FROM RECOMMENDATION "
            "WHERE user_id=? AND status=? ORDER BY rec_id DESC LIMIT ?",
            (user_id, status, limit))
        return [dict(r) for r in rows]

    def get_recommendation(self, rec_id: int) -> Optional[dict]:
        rows = self._query(
            f"SELECT {self._REC_COLS} FROM RECOMMENDATION WHERE rec_id=?",
            (rec_id,))
        return dict(rows[0]) if rows else None

    def count_recommendations(self, user_id: int, status: str = "PENDING") -> int:
        rows = self._query(
            "SELECT COUNT(*) AS n FROM RECOMMENDATION WHERE user_id=? AND status=?",
            (user_id, status))
        return int(rows[0]["n"]) if rows else 0

    def recommendation_signatures(self, user_id: int) -> set:
        """Signatures already PENDING or REJECTED — so we never regenerate a
        suggestion the user has dismissed, nor duplicate a live one."""
        rows = self._query(
            "SELECT signature FROM RECOMMENDATION WHERE user_id=? "
            "AND status IN ('PENDING', 'REJECTED')", (user_id,))
        return {r["signature"] for r in rows}

    def reject_recommendation(self, rec_id: int) -> None:
        with self._write_lock:
            conn = self.get_conn()
            try:
                self._exec(conn, self._q(
                    "UPDATE RECOMMENDATION SET status='REJECTED', decided_at=? "
                    "WHERE rec_id=? AND status='PENDING'"), (now_ms(), rec_id))
                conn.commit()
            finally:
                self.put_conn(conn)

    def accept_recommendation(self, rec: dict, owner: str, user_id: int,
                              tier: str) -> dict:
        """Atomically turn an accepted recommendation into a saved playlist.

        One transaction inserts a (terminated) recommendation-sourced JOB, the
        PLAYLIST, its TRACK + PLAYLIST_TRACK rows, and flips the RECOMMENDATION
        to ACCEPTED — all-or-nothing, so we never leave a half-saved playlist.
        """
        tracks = json.loads(rec["tracks_json"])
        now = now_ms()
        job_id = next_job_id()
        with self._write_lock:
            conn = self.get_conn()
            try:
                cur = conn.cursor()
                # A JOB row records the accepted recommendation in history.
                cur.execute(self._q(
                    "INSERT INTO JOB (job_id, user_id, owner, tier, request_text, "
                    "priority, burst_ms, state, scheduler, created_at, enqueued_at, "
                    "started_at, finished_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'TERMINATED', 'RECOMMENDER', "
                    "?, ?, ?, ?)"),
                    (job_id, user_id, owner, tier, rec["title"], 5, 0,
                     now, now, now, now))
                # PLAYLIST
                cur.execute(self._q(
                    "INSERT INTO PLAYLIST (job_id, title, created_at) "
                    "VALUES (?, ?, ?)"), (job_id, rec["title"], now))
                playlist_id = cur.lastrowid
                # TRACK + PLAYLIST_TRACK
                for pos, t in enumerate(tracks, start=1):
                    cur.execute(self._q(
                        "INSERT INTO TRACK (name, artist, spotify_url, tempo, "
                        "valence, energy) VALUES (?, ?, ?, ?, ?, ?)"),
                        (t["name"], t["artist"], t.get("spotify_url"),
                         t.get("tempo"), t.get("valence"), t.get("energy")))
                    track_id = cur.lastrowid
                    cur.execute(self._q(
                        "INSERT INTO PLAYLIST_TRACK (playlist_id, track_id, position) "
                        "VALUES (?, ?, ?)"), (playlist_id, track_id, pos))
                # RECOMMENDATION — mark accepted and link the saved playlist
                cur.execute(self._q(
                    "UPDATE RECOMMENDATION SET status='ACCEPTED', playlist_id=?, "
                    "decided_at=? WHERE rec_id=?"),
                    (playlist_id, now, rec["rec_id"]))
                conn.commit()
                return {"playlist_id": playlist_id, "job_id": job_id}
            except Exception:
                conn.rollback()
                raise
            finally:
                self.put_conn(conn)

    # ---- helpers ---------------------------------------------------------
    def _exec(self, conn, sql, params):
        if self.backend == "sqlite":
            conn.execute(sql, params)
        else:
            cur = conn.cursor()
            cur.execute(sql, params)
            cur.close()

    def _query(self, sql, params=()):
        sql = self._q(sql)
        conn = self.get_conn()
        try:
            if self.backend == "sqlite":
                cur = conn.execute(sql, params)
                rows = cur.fetchall()
            else:
                cur = conn.cursor(dictionary=True)
                cur.execute(sql, params)
                rows = cur.fetchall()
                cur.close()
            return rows
        finally:
            self.put_conn(conn)
