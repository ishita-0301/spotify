"""Flask web app — UI for the multi-tenant playlist service.

Users submit requests under a service tier (FREE / PREMIUM); the active
scheduler decides whose job runs next; results are real (or mock) Spotify
playlists that can be exported. The analytics page shows fairness across tiers
and users.
"""
from __future__ import annotations

import functools
import json
import threading

from flask import (Flask, Response, jsonify, redirect, render_template,
                   request, session, url_for)

import config
from wms import recommender, spotify_oauth
from wms.database import Database
from wms.engine import Engine
from wms.schedulers import build, ALL_SCHEDULERS
from wms.users import Users, Tier, tier_for_spotify_product


def _rec_payload(rec: dict) -> dict:
    """Shape a RECOMMENDATION row for the dashboard JSON (tracks parsed out)."""
    try:
        tracks = json.loads(rec["tracks_json"])
    except (TypeError, ValueError):
        tracks = []
    return {
        "rec_id": rec["rec_id"],
        "title": rec["title"],
        "reason": rec["reason"],
        "genres": [g for g in (rec.get("genres") or "").split(",") if g],
        "tempo": rec["tempo"],
        "valence": rec["valence"],
        "energy": rec["energy"],
        "tracks": tracks,
    }


def _suggestion_payload(sug: dict) -> dict:
    """Shape a PLAYLIST_SUGGESTION row for the opt-in UI (tracks parsed out)."""
    try:
        tracks = json.loads(sug["tracks_json"])
    except (TypeError, ValueError):
        tracks = []
    return {
        "suggestion_id": sug["suggestion_id"],
        "playlist_id": sug["playlist_id"],
        "job_id": sug["job_id"],
        "genres": [g for g in (sug.get("genres") or "").split(",") if g],
        "status": sug["status"],
        "tracks": tracks,
    }


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

    # Expose the logged-in user (and whether Spotify login is available) to
    # every template, for the nav bar and the login/signup pages.
    @app.context_processor
    def inject_user():
        return {"current_user": current_user(),
                "spotify_login_enabled": spotify_oauth.configured()}

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

    # ---- Spotify OAuth login (optional, supplemental) -------------------
    def _login_error(msg: str, code: int):
        return render_template("login.html", error=msg, name="", next=""), code

    @app.route("/auth/spotify/login")
    def spotify_login():
        """Kick off the Spotify authorization-code flow."""
        if current_user():
            return redirect(url_for("index"))
        if not spotify_oauth.configured():
            return _login_error(
                "Spotify login is not configured on this server.", 503)
        state = spotify_oauth.new_state()
        session["spotify_oauth_state"] = state
        return redirect(spotify_oauth.authorize_url(state))

    @app.route("/auth/spotify/callback")
    def spotify_callback():
        """Handle Spotify's redirect: verify state, exchange the code, map the
        profile onto a local account, and log the user in."""
        if current_user():
            return redirect(url_for("index"))
        if request.args.get("error"):
            return _login_error(
                f"Spotify login was cancelled ({request.args['error']}).", 400)
        expected = session.pop("spotify_oauth_state", None)
        if not request.args.get("state") or request.args["state"] != expected:
            return _login_error("Spotify login failed (state mismatch).", 400)
        code = request.args.get("code")
        if not code:
            return _login_error("Spotify login failed (missing code).", 400)
        try:
            token = spotify_oauth.exchange_code(code)
            profile = spotify_oauth.fetch_profile(token)
        except Exception as exc:  # network / Spotify error — stay graceful
            return _login_error(f"Spotify login failed ({exc}).", 502)
        tier = tier_for_spotify_product(profile["product"])
        user = users.get_or_create_spotify(
            profile["spotify_id"], profile["display_name"],
            profile.get("email"), tier)
        session["user_id"] = user["user_id"]
        return redirect(url_for("index"))

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

    # ---- AI recommendations ---------------------------------------------
    @app.route("/api/recommendations")
    @login_required
    def api_recommendations():
        """Auto-generate (top up) and return the user's PENDING suggestions, so
        personalized recommendations appear on the dashboard with no manual
        request. Generation uses the shared, rate-limited providers."""
        user = current_user()
        recs = recommender.ensure(db, user)
        return jsonify({"recommendations": [_rec_payload(r) for r in recs]})

    @app.route("/api/recommendations/refresh", methods=["POST"])
    @login_required
    def refresh_recommendations():
        user = current_user()
        recommender.generate(db, user)
        recs = db.list_recommendations(user["user_id"], "PENDING")
        return jsonify({"recommendations": [_rec_payload(r) for r in recs]})

    @app.route("/api/recommendations/<int:rec_id>/accept", methods=["POST"])
    @login_required
    def accept_recommendation(rec_id: int):
        user = current_user()
        rec = db.get_recommendation(rec_id)
        if not rec or rec["user_id"] != user["user_id"]:
            return jsonify({"error": "not found"}), 404
        if rec["status"] != "PENDING":
            return jsonify({"error": "already decided"}), 409
        result = db.accept_recommendation(
            rec, owner=user["name"], user_id=user["user_id"], tier=user["tier"])
        return jsonify({"ok": True, **result})

    @app.route("/api/recommendations/<int:rec_id>/reject", methods=["POST"])
    @login_required
    def reject_recommendation(rec_id: int):
        user = current_user()
        rec = db.get_recommendation(rec_id)
        if not rec or rec["user_id"] != user["user_id"]:
            return jsonify({"error": "not found"}), 404
        db.reject_recommendation(rec_id)
        return jsonify({"ok": True})

    # ---- genre-based opt-in suggestions ---------------------------------
    @app.route("/api/playlist/<int:job_id>/suggestions")
    @login_required
    def playlist_suggestions(job_id: int):
        """Return the PENDING genre-matched tracks offered for this playlist (if
        any), so the user can decide whether to append them. Nothing is applied
        here — the playlist stays exactly as generated until a confirm."""
        user = current_user()
        pl = db.playlist_for_job(job_id)
        if not pl:
            return jsonify({"suggestion": None})
        sug = db.pending_suggestion_for_playlist(pl["playlist_id"])
        if not sug or sug["user_id"] != user["user_id"]:
            return jsonify({"suggestion": None})
        return jsonify({"suggestion": _suggestion_payload(sug)})

    @app.route("/api/suggestions/<int:suggestion_id>/confirm", methods=["POST"])
    @login_required
    def confirm_suggestion(suggestion_id: int):
        """Apply the user's decision on a genre suggestion. "Yes" appends the
        tracks to the playlist (ACID); "No" keeps the playlist unchanged."""
        user = current_user()
        sug = db.get_playlist_suggestion(suggestion_id)
        if not sug or sug["user_id"] != user["user_id"]:
            return jsonify({"error": "not found"}), 404
        if sug["status"] != "PENDING":
            return jsonify({"error": "already decided"}), 409
        decision = (request.form.get("decision")
                    or request.args.get("decision") or "")
        if not decision and request.is_json:
            decision = (request.get_json(silent=True) or {}).get("decision", "")
        decision = decision.strip().lower()
        if decision in ("yes", "accept", "append", "true"):
            result = db.accept_playlist_suggestion(sug)
            return jsonify({"ok": True, "decision": "accepted", **result})
        if decision in ("no", "reject", "keep", "false"):
            db.reject_playlist_suggestion(suggestion_id)
            return jsonify({"ok": True, "decision": "rejected"})
        return jsonify({"error": "decision must be 'yes' or 'no'"}), 400

    return app
