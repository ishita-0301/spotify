"""Flask web app — UI for the multi-tenant playlist service.

Users submit requests under a service tier (FREE / PREMIUM); the active
scheduler decides whose job runs next; results are real (or mock) Spotify
playlists that can be exported. The analytics page shows fairness across tiers
and users.
"""
from __future__ import annotations

import threading

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, url_for)

import config
from wms.database import Database
from wms.engine import Engine
from wms.schedulers import build, ALL_SCHEDULERS
from wms.users import Users, Tier


def estimate_burst(text: str) -> int:
    base = 250 + len(text) * 6 + text.count(",") * 120
    return int(max(200, min(900, base)))


def create_app() -> Flask:
    app = Flask(__name__)

    db = Database()
    db.init_schema()
    from wms import models
    models.seed_job_ids(db.max_job_id() + 1)

    users = Users(db)
    weight_fn = users.weight_of

    engine = Engine(db, build("WFQ", config.RR_QUANTUM_MS, weight_fn))
    engine.start()

    state = {"scheduler": "WFQ"}
    lock = threading.Lock()

    def set_active(name: str) -> None:
        with lock:
            state["scheduler"] = name
            engine.set_scheduler(build(name, config.RR_QUANTUM_MS, weight_fn))

    # ---- pages ----------------------------------------------------------
    @app.route("/")
    def index():
        return render_template(
            "index.html", schedulers=ALL_SCHEDULERS, active=state["scheduler"],
            provider_mode=config.PROVIDER_MODE, db_backend=config.DB_BACKEND)

    @app.route("/submit", methods=["POST"])
    def submit():
        name = (request.form.get("owner") or "guest").strip()
        tier = request.form.get("tier", Tier.FREE)
        text = (request.form.get("request_text") or "").strip()
        priority = int(request.form.get("priority") or 5)
        if text:
            user = users.get_or_create(name, tier)
            engine.submit(user["name"], text, priority=priority,
                          burst_ms=estimate_burst(text),
                          user_id=user["user_id"], tier=user["tier"])
        return redirect(url_for("index"))

    @app.route("/scheduler", methods=["POST"])
    def set_scheduler():
        name = request.form.get("scheduler", "WFQ")
        if name in ALL_SCHEDULERS:
            set_active(name)
        return redirect(url_for("index"))

    @app.route("/demo", methods=["POST"])
    def demo():
        """Seed the classic fairness scenario: a FREE 'spammer' floods the queue,
        while a quiet FREE user and a PREMIUM user submit a couple each. Watch
        how the active scheduler shares the service."""
        spammer = users.get_or_create("spammer_sam", Tier.FREE)
        quiet = users.get_or_create("quiet_qi", Tier.FREE)
        vip = users.get_or_create("premium_pat", Tier.PREMIUM)
        for i in range(10):
            engine.submit(spammer["name"], f"party dance hits vol {i}", priority=5,
                          burst_ms=400, user_id=spammer["user_id"], tier=spammer["tier"])
        for i in range(2):
            engine.submit(quiet["name"], "calm ambient sleep sounds", priority=5,
                          burst_ms=400, user_id=quiet["user_id"], tier=quiet["tier"])
            engine.submit(vip["name"], "high energy workout mix", priority=5,
                          burst_ms=400, user_id=vip["user_id"], tier=vip["tier"])
        return redirect(url_for("index"))

    @app.route("/analytics")
    def analytics():
        return render_template(
            "analytics.html", rows=db.analytics(),
            by_tier=db.analytics_by_tier(), by_user=db.analytics_by_user())

    @app.route("/benchmark", methods=["POST"])
    def run_benchmark():
        def _run():
            import benchmark
            benchmark.main()
        threading.Thread(target=_run, daemon=True).start()
        return redirect(url_for("analytics"))

    # ---- playlist view + export -----------------------------------------
    @app.route("/playlist/<int:job_id>")
    def playlist(job_id: int):
        pl = db.playlist_for_job(job_id)
        return render_template("playlist.html", pl=pl, job_id=job_id)

    @app.route("/export/<int:job_id>.m3u")
    def export_m3u(job_id: int):
        pl = db.playlist_for_job(job_id)
        lines = ["#EXTM3U"]
        if pl:
            for t in pl["tracks"]:
                lines.append(f"#EXTINF:-1,{t['artist']} - {t['name']}")
                lines.append(t.get("spotify_url") or f"# {t['name']} by {t['artist']}")
        body = "\n".join(lines) + "\n"
        return Response(body, mimetype="audio/x-mpegurl", headers={
            "Content-Disposition": f'attachment; filename="playlist_{job_id}.m3u"'})

    # ---- JSON for the live dashboard ------------------------------------
    @app.route("/api/jobs")
    def api_jobs():
        jobs = db.list_jobs(limit=80)
        for j in jobs:
            j["playlist"] = db.playlist_for_job(j["job_id"])
        return jsonify(jobs)

    @app.route("/api/analytics")
    def api_analytics():
        return jsonify({"by_scheduler": db.analytics(),
                        "by_tier": db.analytics_by_tier()})

    return app
