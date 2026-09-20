"""Gemini provider — real mood analysis with a keyword-based mock fallback.

`analyze(request_text)` turns free text into structured music attributes
(genres, tempo, valence, energy). Real mode calls the Gemini API when
`GEMINI_API_KEY` is set; otherwise the deterministic keyword mock is used, so
the system runs with no key.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

import config
from ._http import urlopen

# --- mock keyword profiles --------------------------------------------------
_PROFILES = {
    "study":   {"genres": ["lo-fi", "ambient"],      "tempo": 85,  "valence": 0.4, "energy": 0.3},
    "focus":   {"genres": ["lo-fi", "instrumental"], "tempo": 90,  "valence": 0.4, "energy": 0.35},
    "relax":   {"genres": ["chill", "acoustic"],     "tempo": 70,  "valence": 0.5, "energy": 0.25},
    "calm":    {"genres": ["ambient", "piano"],      "tempo": 65,  "valence": 0.5, "energy": 0.2},
    "sleep":   {"genres": ["ambient", "piano"],      "tempo": 60,  "valence": 0.4, "energy": 0.15},
    "workout": {"genres": ["edm", "hip-hop"],        "tempo": 140, "valence": 0.7, "energy": 0.9},
    "gym":     {"genres": ["edm", "trap"],           "tempo": 145, "valence": 0.7, "energy": 0.92},
    "party":   {"genres": ["dance", "pop"],          "tempo": 125, "valence": 0.85, "energy": 0.88},
    "happy":   {"genres": ["pop", "funk"],           "tempo": 120, "valence": 0.9, "energy": 0.75},
    "sad":     {"genres": ["indie", "acoustic"],     "tempo": 80,  "valence": 0.2, "energy": 0.3},
    "romantic":{"genres": ["r&b", "soul"],           "tempo": 95,  "valence": 0.7, "energy": 0.45},
    "drive":   {"genres": ["rock", "synthwave"],     "tempo": 115, "valence": 0.6, "energy": 0.7},
    "coding":  {"genres": ["lo-fi", "electronic"],   "tempo": 100, "valence": 0.5, "energy": 0.5},
}
_DEFAULT = {"genres": ["pop"], "tempo": 110, "valence": 0.5, "energy": 0.5}


def _use_real() -> bool:
    mode = config.PROVIDER_MODE
    if mode == "mock":
        return False
    if mode == "real":
        return True
    return bool(config.GEMINI_API_KEY)


def _real_analyze(text: str) -> Optional[dict]:
    prompt = (
        "You map a music mood request to attributes. Respond with ONLY compact "
        "JSON: {\"genres\":[up to 3 strings],\"tempo\":BPM number,"
        "\"valence\":0..1,\"energy\":0..1}. Request: " + text
    )
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODEL}:generateContent?key={config.GEMINI_API_KEY}")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode())
    raw = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):raw.rfind("}") + 1]
    attrs = json.loads(raw)
    return {
        "genres": [str(g) for g in attrs.get("genres", [])][:3] or _DEFAULT["genres"],
        "tempo": float(attrs.get("tempo", 110)),
        "valence": float(attrs.get("valence", 0.5)),
        "energy": float(attrs.get("energy", 0.5)),
    }


def _mock_analyze(text: str) -> dict:
    text = (text or "").lower()
    matched = [p for kw, p in _PROFILES.items() if kw in text]
    if not matched:
        return dict(_DEFAULT)
    genres: list[str] = []
    tempo = valence = energy = 0.0
    for p in matched:
        for g in p["genres"]:
            if g not in genres:
                genres.append(g)
        tempo += p["tempo"]; valence += p["valence"]; energy += p["energy"]
    n = len(matched)
    return {
        "genres": genres[:3],
        "tempo": round(tempo / n, 1),
        "valence": round(valence / n, 2),
        "energy": round(energy / n, 2),
    }


def analyze(request_text: str) -> dict:
    attrs = None
    if _use_real():
        try:
            attrs = _real_analyze(request_text)
        except Exception as exc:
            print(f"[gemini] real analyze failed ({exc}); using mock")
    if attrs is None:
        attrs = _mock_analyze(request_text)
    attrs["query"] = request_text
    return attrs
