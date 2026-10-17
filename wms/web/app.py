"""Flask web app — UI for the multi-tenant playlist service.

Users submit requests under a service tier (FREE / PREMIUM); the active
scheduler decides whose job runs next; results are real (or mock) Spotify
playlists that can be exported. The analytics page shows fairness across tiers
and users.
"""
from __future__ import annotations

import functools
import threading

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, url_for)

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
    app.secret_key = config.SECRET_KEY

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

    # ---- authentication -------------------------------------------------
    def current_user():
        uid = session.get("user_id")
        return users.by_id(uid) if uid is not None else None

    def login_required(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if current_user() is None:
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    # Expose the logged-in user to every template (for the nav bar).
    @app.context_processor
    def inject_user():
        return {"current_user": current_user()}

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if current_user():
            return redirect(url_for("index"))
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            password = request.form.get("password") or ""
            tier = request.form.get("tier", Tier.FREE)
            try:
                user = users.register(name, password, tier)
            except ValueError as e:
                return render_template("signup.html", error=str(e), name=name), 400
            session["user_id"] = user["user_id"]
            return redirect(url_for("index"))
        return render_template("signup.html", error=None, name="")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user():
            return redirect(url_for("index"))
        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            password = request.form.get("password") or ""
            user = users.authenticate(name, password)
            if user is None:
                return render_template(
                    "login.html", error="Invalid username or password.",
                    name=name), 401
            session["user_id"] = user["user_id"]
            nxt = request.args.get("next") or request.form.get("next")
            return redirect(nxt or url_for("index"))
        return render_template("login.html", error=None, name="",
                               next=request.args.get("next", ""))

    @app.route("/logout", methods=["POST"])
    def logout():
        session.pop("user_id", None)
        return redirect(url_for("login"))

    # ---- pages ----------------------------------------------------------
    @app.route("/")
    @login_required
    def index():
        return render_template(
            "index.html", schedulers=ALL_SCHEDULERS, active=state["scheduler"],
            provider_mode=config.PROVIDER_MODE, db_backend=config.DB_BACKEND)

    @app.route("/submit", methods=["POST"])
    @login_required
    def submit():
        user = current_user()
        text = (request.form.get("request_text") or "").strip()
        priority = int(request.form.get("priority") or 5)
        if text:
            engine.submit(user["name"], text, priority=priority,
                          burst_ms=estimate_burst(text),
                          user_id=user["user_id"], tier=user["tier"])
        return redirect(url_for("index"))

    @app.route("/scheduler", methods=["POST"])
    @login_required
    def set_scheduler():
        name = request.form.get("scheduler", "WFQ")
        if name in ALL_SCHEDULERS:
            set_active(name)
        return redirect(url_for("index"))

    @app.route("/demo", methods=["POST"])
    @login_required
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
    @login_required
    def analytics():
        return render_template(
            "analytics.html", rows=db.analytics(),
            by_tier=db.analytics_by_tier(), by_user=db.analytics_by_user())

    @app.route("/benchmark", methods=["POST"])
    @login_required
    def run_benchmark():
        def _run():
            import benchmark
            benchmark.main()
        threading.Thread(target=_run, daemon=True).start()
        return redirect(url_for("analytics"))

    # ---- playlist view + export -----------------------------------------
    @app.route("/playlist/<int:job_id>")
    @login_required
    def playlist(job_id: int):
        pl = db.playlist_for_job(job_id)
        return render_template("playlist.html", pl=pl, job_id=job_id)

    @app.route("/export/<int:job_id>.m3u")
    @login_required
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
    @login_required
    def api_jobs():
        jobs = db.list_jobs(limit=80)
        for j in jobs:
            j["playlist"] = db.playlist_for_job(j["job_id"])
        return jsonify(jobs)

    @app.route("/api/analytics")
    @login_required
    def api_analytics():
        return jsonify({"by_scheduler": db.analytics(),
                        "by_tier": db.analytics_by_tier(),
                        "by_user": db.analytics_by_user()})

    return app
