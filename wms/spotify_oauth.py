"""Spotify OAuth 2.0 (authorization-code) login — optional, supplemental auth.

Lets a user sign in with their Spotify account instead of creating a local
password account. Unlike catalog *search* (which Spotify blocks without the app
owner's Premium — see CLAUDE.md), the authorize + ``/v1/me`` profile endpoints
work for any registered account, so this is a genuine alternative login path.

It is entirely OPTIONAL: when no client credentials are configured the flow is
unavailable (``configured()`` is False) and the rest of the app keeps running
fully offline. All network I/O reuses the shared SSL-aware ``urlopen`` helper.
"""
from __future__ import annotations

import base64
import json
import secrets
import urllib.parse
import urllib.request

import config
from .providers._http import urlopen

_AUTH_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_PROFILE_URL = "https://api.spotify.com/v1/me"


def configured() -> bool:
    """True when enough is set up to offer the Spotify login button."""
    return bool(config.SPOTIFY_OAUTH_CLIENT_ID
                and config.SPOTIFY_OAUTH_CLIENT_SECRET
                and config.SPOTIFY_OAUTH_REDIRECT_URI)


def new_state() -> str:
    """An unguessable anti-CSRF token to round-trip through the redirect."""
    return secrets.token_urlsafe(24)


def authorize_url(state: str) -> str:
    """The Spotify consent-screen URL to send the user to."""
    params = {
        "client_id": config.SPOTIFY_OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_OAUTH_REDIRECT_URI,
        "scope": config.SPOTIFY_OAUTH_SCOPES,
        "state": state,
    }
    return _AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str) -> str:
    """Trade the authorization ``code`` for an access token."""
    creds = f"{config.SPOTIFY_OAUTH_CLIENT_ID}:{config.SPOTIFY_OAUTH_CLIENT_SECRET}"
    basic = base64.b64encode(creds.encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.SPOTIFY_OAUTH_REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data, headers={
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode())
    return payload["access_token"]


def fetch_profile(access_token: str) -> dict:
    """Fetch the signed-in user's Spotify profile (id, name, email, tier)."""
    req = urllib.request.Request(
        _PROFILE_URL, headers={"Authorization": f"Bearer {access_token}"})
    with urlopen(req, timeout=10) as resp:
        p = json.loads(resp.read().decode())
    return {
        "spotify_id": p.get("id"),
        "display_name": p.get("display_name") or p.get("id"),
        "email": p.get("email"),
        # "premium" | "free" | "open" — maps to our service tier.
        "product": (p.get("product") or "").lower(),
    }
