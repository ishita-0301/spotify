# Phase 2 — Login & Live Dashboard

Changes and updates made after the initial build (Team CodingLeague, T-229).

> Filename note: requested as `Phase@.md`; created as `Phase2.md` (`@` = Shift+2).
> Rename if the literal `@` was intended.

This phase added **user authentication** (the web app is now a real multi-user
login system, not an open form) and made the **dashboard and analytics update
live**. Both are UI-layer work; the OS scheduler and DBMS transaction layers were
left untouched.

---

## 1. Authentication (login / signup / logout)

Every page of the web app is now gated behind a login. A job's owner and tier
come from the logged-in account instead of free-text form fields.

**New files**

- `wms/auth.py` — password hashing with the standard library only
  (PBKDF2-HMAC-SHA256, per-user random salt). `hash_password()` /
  `verify_password()`; stored as `algo$iterations$salt$digest`.
- `wms/web/templates/login.html` — log-in page.
- `wms/web/templates/signup.html` — sign-up page (username, password, tier).

**Changed files**

- `db/schema_sqlite.sql`, `db/schema_mysql.sql` — added `USER.password_hash`.
- `wms/database.py` — `list_users` / `insert_user` carry the hash;
  `_ensure_password_column` migrates databases created before auth existed
  (idempotent `ALTER TABLE`, run from `init_schema`).
- `wms/users.py` — new `register()` (rejects blank/duplicate usernames),
  `authenticate()`, and `by_id()`. `get_or_create()` is kept for the
  passwordless users seeded by the demo and benchmark.
- `config.py` — `SECRET_KEY` (env-overridable via `WMS_SECRET_KEY`) to sign the
  session cookie.
- `wms/web/app.py` — `app.secret_key`; `/signup`, `/login`, `/logout` routes;
  a `login_required` decorator gating `/`, `/submit`, `/scheduler`, `/demo`,
  `/analytics`, `/benchmark`, `/playlist`, `/export`, and both `/api/*`
  endpoints; `current_user()` injected into every template. `submit()` now
  takes owner/tier from the session user.
- `wms/web/templates/base.html` — nav shows the logged-in user + tier + a
  **Log out** button (or Log in / Sign up when logged out).
- `wms/web/templates/index.html` — removed the name/tier inputs; the submit form
  now shows who you're submitting as.
- `.env.example` — documented `WMS_SECRET_KEY`.

**Security notes**

- Passwords are never stored in plaintext — salted PBKDF2 hashes only.
- `verify_password` uses a constant-time comparison.
- The default `SECRET_KEY` is a dev value; set `WMS_SECRET_KEY` for any non-local
  use.

---

## 2. Live dashboard (auto-update)

**Changed files**

- `wms/web/app.py` — `/api/analytics` now also returns `by_user` (previously only
  `by_scheduler` and `by_tier`), which the live by-user table needs.
- `wms/web/templates/analytics.html` — the three tables (by scheduler / tier /
  user) now poll `/api/analytics` every 2s and re-render in place; the
  server-rendered tables remain as the initial / no-JS fallback.
- `wms/web/templates/index.html` — kept the existing 1s `/api/jobs` polling;
  added the shared liveness indicator.
- Both pages — a `● live · updated Xs ago` indicator that ticks every second and
  resets on each successful fetch (so a stalled connection is visible).
- `wms/web/static/style.css` — styles for the auth forms (`.panel.narrow`,
  `.stack`, `.error`), the nav user chip, and the `.live` indicator.

---

## 3. Documentation

- `CLAUDE.md` — added `auth.py` to the layout, noted the login gate and
  passwordless seed users in Conventions.
- `BUILD_GUIDE.md` — updated the run instructions, added **Accounts & login** and
  **Live dashboard** sections, noted `password_hash` + migration in Database, and
  extended the testing checklist.

---

## How to verify

```bash
python run.py          # redirects to /login; Sign up, then use the app
python benchmark.py    # still runs headless with seeded users, no login
```

Checklist:

- [ ] Logged out, any page redirects to `/login`; wrong password is rejected.
- [ ] Sign up creates an account and logs in; Log out returns to login.
- [ ] A submitted job is owned by the logged-in account's name and tier.
- [ ] Dashboard and `/analytics` update with no reload; the live indicator ticks.
- [ ] Runs offline with mock providers and no API keys.
