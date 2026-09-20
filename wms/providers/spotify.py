"""Spotify provider — real search (client-credentials) with mock fallback.

Real mode uses the Spotify **client-credentials** flow, which needs only the
app's Client ID/Secret — no user login and no Premium — to search the real
catalog. If credentials are absent (or a call fails) it falls back to the
offline mock so the system always runs.
"""
from __future__ import annotations

import base64
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List

import config
from ._http import urlopen

# --- mock data --------------------------------------------------------------
_ADJECTIVES = [
    "Midnight", "Golden", "Electric", "Velvet", "Neon", "Silent", "Crimson",
    "Lunar", "Paper", "Glass", "Wild", "Hollow", "Crystal", "Distant", "Faded",
]
_NOUNS = [
    "Echoes", "Skyline", "Tides", "Embers", "Mirrors", "Horizon", "Currents",
    "Daydream", "Static", "Gravity", "Lanterns", "Motion", "Signals", "Bloom",
]
_ARTISTS = [
    "The Wandering Keys", "Aria Lune", "Blue Circuit", "Novaform", "Kite & Coast",
    "Hana Reef", "Ember Fields", "The Slow Parade", "Cassette Bloom", "Vega North",
]

# --- real client-credentials token cache ------------------------------------
_token = {"value": None, "expires": 0.0}
_token_lock = threading.Lock()

# Set once if the app is Forbidden (e.g. owner lacks Premium) so we stop
# retrying the real API every job and serve the mock immediately.
_real_disabled = False
_disabled_reason = ""


def _use_real() -> bool:
    if _real_disabled:
        return False
    mode = config.PROVIDER_MODE
    if mode == "mock":
        return False
    if mode == "real":
        return True
    return bool(config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET)


def _get_token() -> str:
    with _token_lock:
        if _token["value"] and time.time() < _token["expires"] - 30:
            return _token["value"]
        creds = f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}"
        basic = base64.b64encode(creds.encode()).decode()
        data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
        req = urllib.request.Request(
            "https://accounts.spotify.com/api/token", data=data,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
        _token["value"] = payload["access_token"]
        _token["expires"] = time.time() + payload.get("expires_in", 3600)
        return _token["value"]


def _real_search(attrs: dict, n: int) -> List[dict]:
    genres = attrs.get("genres") or []
    query = " ".join(genres[:2]) if genres else attrs.get("query", "music")
    url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode(
        {"q": query, "type": "track", "limit": max(1, min(50, n))})
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_get_token()}"})
    with urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read().decode())

    items = payload.get("tracks", {}).get("items", [])
    # Spotify deprecated the per-track audio-features endpoint for new apps, so
    # we attach the requested attributes as the playlist's target vibe.
    tempo = float(attrs.get("tempo", 110))
    valence = float(attrs.get("valence", 0.5))
    energy = float(attrs.get("energy", 0.5))
    tracks = []
    for it in items[:n]:
        tracks.append({
            "name": it.get("name", "Unknown"),
            "artist": ", ".join(a["name"] for a in it.get("artists", [])) or "Unknown",
            "spotify_url": it.get("external_urls", {}).get("spotify"),
            "tempo": tempo, "valence": valence, "energy": energy,
        })
    if not tracks:
        raise RuntimeError("no results from Spotify")
    return tracks


def _mock_search(attrs: dict, n: int) -> List[dict]:
    seed = hash((attrs.get("query"), tuple(attrs.get("genres", [])))) & 0xFFFFFFFF
    rng = random.Random(seed)
    base_tempo = float(attrs.get("tempo", 110))
    base_valence = float(attrs.get("valence", 0.5))
    base_energy = float(attrs.get("energy", 0.5))

    def clamp(x): return round(max(0.0, min(1.0, x)), 2)

    tracks = []
    for _ in range(n):
        tracks.append({
            "name": f"{rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)}",
            "artist": rng.choice(_ARTISTS),
            "spotify_url": None,
            "tempo": round(base_tempo + rng.uniform(-8, 8), 1),
            "valence": clamp(base_valence + rng.uniform(-0.12, 0.12)),
            "energy": clamp(base_energy + rng.uniform(-0.12, 0.12)),
        })
    return tracks


def search(attrs: dict, n: int = 10) -> List[dict]:
    global _real_disabled, _disabled_reason
    if _use_real():
        try:
            return _real_search(attrs, n)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # App not authorized (e.g. owner lacks Premium) — stop retrying.
                _real_disabled = True
                _disabled_reason = f"HTTP {exc.code}"
                print(f"[spotify] real API disabled for this session ({exc.code}); "
                      f"using mock. Owner Premium likely required.")
            else:
                print(f"[spotify] real search failed ({exc}); using mock")
        except Exception as exc:
            print(f"[spotify] real search failed ({exc}); using mock")
    return _mock_search(attrs, n)
