# -*- coding: utf-8 -*-
"""Upload cracked.json to the server over tailscale via SCP, with fallbacks."""
import subprocess
import os
import time

import config
from cracked_store import load_cracked, new_since, \
    load_upload_state, save_upload_state
from network import tailscale


def run(cmd, timeout=60):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def upload(progress_cb=None):
    """Upload cracked.json to server:/home/sun/handshake/. Returns dict.

    progress_cb(msg) - status lines
    """
    if progress_cb:
        progress_cb("Checking tailscale...")
    if not tailscale.available():
        return {"ok": False, "error": "tailscale not installed"}
    if tailscale.status() != "up":
        if not tailscale.up():
            return {"ok": False, "error": "tailscale down / needs auth"}
    if not tailscale.ping_server():
        return {"ok": False, "error": "server unreachable over tailscale"}

    if not os.path.exists(config.CRACKED_FILE):
        return {"ok": False, "error": "no cracked.json yet"}

    # Incremental: only send entries newer than the last successful upload.
    state = load_upload_state()
    last = state.get("last", 0)
    new_entries = new_since(last)
    if not new_entries:
        return {"ok": True, "uploaded": 0, "error": None, "new": False}

    # Write a temp file containing only the new entries.
    tmp_file = config.CRACKED_FILE + ".upload.tmp"
    try:
        import json as _json
        with open(tmp_file, "w") as f:
            _json.dump(new_entries, f, indent=2)
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "could not write upload temp file"}

    if progress_cb:
        progress_cb("SCP %d new to %s..." % (len(new_entries),
                                             config.UPLOAD_SERVER))

    dest = "%s@%s:%s" % (config.UPLOAD_USER, config.UPLOAD_SERVER,
                         config.HANDSHAKE_REMOTE_DIR)
    cmd = ["scp"] + config.UPLOAD_SCP_OPTIONS + [tmp_file, dest]

    # retry with backoff
    for attempt in range(1, 4):
        r = run(cmd)
        if r is not None and r.returncode == 0:
            state["last"] = int(time.time())
            save_upload_state(state)
            try:
                os.remove(tmp_file)
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True, "uploaded": len(new_entries), "error": None,
                    "new": True}
        if progress_cb:
            progress_cb("Retry %d/3..." % attempt)
        time.sleep(5)
    try:
        os.remove(tmp_file)
    except Exception:  # noqa: BLE001
        pass

    # Determine likely cause
    err = r.stderr if r else "SCP failed"
    low = (err or "").lower()
    if "no such file or directory" in low:
        error = "server path missing: %s" % config.HANDSHAKE_REMOTE_DIR
    elif "permission denied" in low:
        error = "auth denied (check ssh keys)"
    elif "no route" in low or "timed out" in low:
        error = "tailscale/server unreachable"
    else:
        error = "scp failed: %s" % (err or "").strip()[:50]
    return {"ok": False, "error": error}
