# -*- coding: utf-8 -*-
"""Tailscale status/connectivity helpers with graceful fallbacks."""
import subprocess
import shutil
import time

import config

# status() is polled from the render loop, so cache it — a subprocess spawn
# per frame is what makes the LCD lag. Refreshed at most once per interval.
_STATUS_TTL = 15.0
_status_cache = {"t": 0.0, "v": None}


def run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def available():
    return shutil.which("tailscale") is not None


def status(max_age=_STATUS_TTL):
    """Return one of: 'up', 'down', 'logged_out', 'no_tailscale' (cached)."""
    now = time.time()
    if _status_cache["v"] is not None and now - _status_cache["t"] < max_age:
        return _status_cache["v"]
    v = _status_uncached()
    _status_cache["t"] = now
    _status_cache["v"] = v
    return v


def _status_uncached():
    if not available():
        return "no_tailscale"
    r = run(["tailscale", "status"])
    if r is None:
        return "down"
    out = r.stdout.lower()
    if "logged out" in out:
        return "logged_out"
    if "tailscale" in out or "on" in out:
        return "up"
    if r.returncode == 0 and out.strip():
        return "up"
    return "down"


def ping_server():
    """True if the upload server responds over tailscale."""
    r = run(["tailscale", "ping", "-c", "1", "-t", "3", config.UPLOAD_SERVER])
    return bool(r and r.returncode == 0)


def up(non_interactive=True):
    """Bring tailscale up (user must already be authed)."""
    if not available():
        return False
    if status() == "up":
        return True
    args = ["sudo", "tailscale", "up"]
    if non_interactive:
        args.append("--reset")
    r = run(args, timeout=30)
    return bool(r and r.returncode == 0)


def reachable():
    """Overall readiness: tailscale up AND server pingable."""
    if status() != "up":
        return False
    return ping_server()


def icon():
    """Return (char, color) for status bar."""
    from ui import theme
    s = status()
    if s == "up":
        ok = ping_server()
        return ("TS", accent() if ok else highlight())
    if s == "logged_out":
        return ("TS", theme.palette()["highlight"])
    return ("TS", (255, 0, 0))


def accent():
    from ui import theme
    return theme.accent_color()
