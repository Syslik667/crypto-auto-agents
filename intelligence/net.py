"""Bounded, HTTPS-only reads. Credentialed requests never follow redirects."""
import hashlib
import json
import math
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

UA = "CryptoAutoAgents/2.0 (+https://github.com/Syslik667/crypto-auto-agents)"
blocked = set()


def canonical(url):
    """Only publish clean public paths. Discard query strings and fragments."""
    try:
        p = urlsplit(url)
        if (p.scheme != "https" or not p.hostname or p.username is not None
                or p.port not in (443, None) or not re.fullmatch(r"[a-z0-9.-]+", p.hostname)
                or not re.fullmatch(r"[/a-zA-Z0-9_~.%+-]*", p.path)
                or re.search(r"%(?:40|2f|5c|00|0a|0d)", p.path, re.I)):
            return ""
        return urlunsplit(("https", p.hostname, p.path.rstrip("/") or "/", "", ""))
    except (TypeError, ValueError):
        return ""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()[:18]


def number(v):
    try:
        n = float(v)
        return n if not isinstance(v, bool) and math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (not newurl.startswith("https://") or req.has_header("Authorization")
                or urlsplit(req.full_url).hostname == "api.telegram.org"):
            raise ValueError("Redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit=2_000_000, *, headers=None, data=None):
    host = urlsplit(url).hostname
    if not url.startswith("https://") or not host:
        raise ValueError("HTTPS required")
    if host in blocked:
        raise RuntimeError("Host quota reached")
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})}, data=data)
    try:
        with urllib.request.build_opener(Redirect()).open(req, timeout=6) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("Response too large")
            return raw
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429) or (exc.headers and exc.headers.get("Retry-After")):
            blocked.add(host)
        raise


def read_json(url, **kwargs):
    return json.loads(fetch(url, **kwargs))


def failure(exc):
    # Exception messages can contain a bot token, URL or response body.
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__
