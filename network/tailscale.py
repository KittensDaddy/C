# -*- coding: utf-8 -*-
"""Tailscale status/connectivity helpers with graceful fallbacks."""
import subprocess
import shutil
import threading
import time

import config

# `tailscale status` spawns a subprocess that BLOCKS ~2s when the daemon is down
# (it's on-demand here). Calling that from a render — while holding the display
# lock — froze the whole UI. So a background thread polls it and status() only
# ever returns the cached value; it never spawns inline.
_STATUS_TTL = 10.0
_status_cache = {"t": 0.0, "v": "no_tailscale"}
_poller_started = False
_poller_lock = threading.Lock()


def run(cmd, timeout=20):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def available():
    return shutil.which("tailscale") is not None


def _poll_loop():
    while True:
        _status_cache["v"] = _status_uncached()
        _status_cache["t"] = time.time()
        time.sleep(_STATUS_TTL)


def start_poller():
    """Start the background status poller once (idempotent)."""
    global _poller_started
    with _poller_lock:
        if _poller_started:
            return
        _poller_started = True
        threading.Thread(target=_poll_loop, daemon=True).start()


def status(max_age=None):
    """Cached status: 'up' | 'down' | 'logged_out' | 'no_tailscale'. Never
    blocks — returns the last value the background poller fetched."""
    start_poller()
    return _status_cache["v"]

def refresh_status():
    """Force a synchronous refresh (use off the render path, e.g. after `up`)."""
    _status_cache["v"] = _status_uncached()
    _status_cache["t"] = time.time()
    return _status_cache["v"]


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
    ok = bool(r and r.returncode == 0)
    if ok:
        refresh_status()          # update the cache immediately (not per-frame)
    return ok


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
