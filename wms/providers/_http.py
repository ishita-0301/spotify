"""Small HTTP helper with a properly configured SSL context.

Some Python builds (notably msys2/ucrt on Windows) ship without a usable CA
bundle, so HTTPS verification fails. We use certifi's bundle when available.
"""
from __future__ import annotations

import ssl
import urllib.request

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # certifi missing — fall back to the system default
    _CTX = ssl.create_default_context()


def urlopen(req, timeout: float = 10):
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX)
