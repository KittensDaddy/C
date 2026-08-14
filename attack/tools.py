# -*- coding: utf-8 -*-
"""Shared helpers for the native engine: tool resolution + logging.

systemd's PATH often lacks /usr/sbin, so resolve binaries to absolute paths with
sensible fallbacks (same approach as attack/scanner.py)."""
import os
import shutil
import subprocess

import config


def which(name, *fallbacks):
    """Absolute path to a tool, or the bare name if nothing resolves."""
    found = shutil.which(name)
    if found:
        return found
    for path in fallbacks:
        if os.path.exists(path):
            return path
    return name


AIRODUMP = which("airodump-ng", "/usr/sbin/airodump-ng")
AIREPLAY = which("aireplay-ng", "/usr/sbin/aireplay-ng")
AIRCRACK = which("aircrack-ng", "/usr/bin/aircrack-ng", "/usr/sbin/aircrack-ng")
REAVER = which("reaver", "/usr/bin/reaver", "/usr/sbin/reaver")
BULLY = which("bully", "/usr/bin/bully", "/usr/sbin/bully")
HCXDUMP = which("hcxdumptool", "/usr/bin/hcxdumptool", "/usr/sbin/hcxdumptool")
HCXPCAP = which("hcxpcapngtool", "/usr/bin/hcxpcapngtool")
HASHCAT = which("hashcat", "/usr/bin/hashcat")
MACCHANGER = which("macchanger", "/usr/bin/macchanger")

# Which tools each capability needs (for SysCheck).
REQUIRED_TOOLS = {
    "airodump-ng": AIRODUMP,
    "aireplay-ng": AIREPLAY,
    "aircrack-ng": AIRCRACK,
    "reaver": REAVER,
    "bully": BULLY,
    "hcxdumptool": HCXDUMP,
    "hashcat": HASHCAT,
}


def tool_ok(path):
    """True if the resolved path exists (absolute) or is on PATH (bare name)."""
    return os.path.isabs(path) and os.path.exists(path) or bool(shutil.which(path))


def log(msg):
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write("[attack] %s\n" % msg)
    except Exception:  # noqa: BLE001
        pass


def run(cmd, timeout=30):
    """Blocking run, capture output; None on failure/timeout."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        log("run failed %s: %s" % (cmd[:2], e))
        return None
