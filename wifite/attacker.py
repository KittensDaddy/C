# -*- coding: utf-8 -*-
"""Build and run wifite2 attacks.

Splits command building into three modes:
  target  – hand-picked ESSID/BSSID, no pillage (-p).
  preset  – Quick Attack: pillage (-p <scan_time>) + preset args, all targets.
  resume  – --resume-latest, no extra flags.
"""
import subprocess
import threading
import time
import json
import os
import re
import select
import shutil

import config
from wifite import interface as iface_mod
from wifite.output import OutputParser, EventType, AttackRequest, AttackResult
from cracked_store import load_cracked, save_cracked


def _which_wifite():
    """Return path to a working wifite binary, testing each candidate."""
    import shutil as _shutil
    candidates = list(config.WIFITE_BIN_CANDIDATES)
    w = _shutil.which("wifite")
    if w and w not in candidates:
        candidates.append(w)
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            result = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                return path
        except Exception:
            continue
    return None


def build_command(iface, preset=None):
    """Backward-compat wrapper – see build_request()."""
    import copy
    req = AttackRequest(
        interface=iface_mod.iface_name(iface),
        preset=preset,
        target_essid=config.Runtime.target_essid,
        target_bssid=config.Runtime.target_bssid,
        exclusions=list(config.Runtime.excluded_essids),
        attack_modes=[dict(x) for x in config.attack_modes],
        timing=[dict(x) for x in config.timing],
        filters=[dict(x) for x in config.filters],
        interface_opts=[dict(x) for x in config.interface_opts],
    )
    return _build_request_cmd(req)


def _build_request_cmd(req):
    """Build wifite argv from an AttackRequest. Never reads globals."""
    wifite = _which_wifite()
    if not wifite:
        return None

    if req.resume_latest:
        return ["sudo", wifite, "--resume-latest"]
    if req.clean_sessions:
        return ["sudo", wifite, "--clean-sessions"]

    # --no-tui forces wifite's classic text output (the format our parser reads);
    # its curses TUI would emit only cursor-positioned draws we can't parse.
    cmd = ["sudo", wifite, "-i", req.interface, "--no-tui"]
    if req.preset:
        # Preset controls attack modes + timing. Config only contributes
        # filters and interface opts to avoid flag duplication.
        _append_filters(cmd, req.filters)
        _append_interface_opts(cmd, req.interface_opts)
        preset_args = list(req.preset.get("args", []))
        targeted = bool(req.target_bssid or req.target_essid)
        # No specific target -> pillage: attack all targets after a scan window
        # (headless boxes can't press a key to stop scanning). When a target was
        # picked (Scan & Attack), attack only it via -b/-e and skip pillage.
        if not targeted and not any(a in ("-p", "--pillage") for a in preset_args):
            cmd += ["-p", str(req.preset.get("scan", 20))]
        cmd.extend(preset_args)
        if req.target_bssid:
            cmd += ["-b", req.target_bssid]
        if req.target_essid:
            cmd += ["-e", req.target_essid]
    elif req.target_essid or req.target_bssid:
        cmd.extend(_config_flags(req, pillage=False))
        if req.target_bssid:
            cmd += ["-b", req.target_bssid]
        if req.target_essid:
            cmd += ["-e", req.target_essid]
    else:
        cmd.extend(_config_flags(req, pillage=True))

    for ex in req.exclusions:
        cmd += ["-E", ex]
    return cmd


def _config_flags(req, pillage=True):
    """Translate a frozen config snapshot into wifite argv.

    pillage=True  → include -p <scan_time> (Quick Attack / Preset).
    pillage=False → omit -p (selected single target).
    """
    flags = []

    # --- attack modes ---
    for opt in req.attack_modes:
        kind = opt.get("kind", "bool")
        if kind == "bool":
            state = bool(opt.get("state"))
            flag = opt.get("flag")
            if not flag:
                continue
            invert = opt.get("invert", False)
            if (not state) if invert else state:
                flags.append(flag)
        elif kind == "cycle":
            if opt["name"] == "WPS Tool" and opt.get("state") == "bully":
                flags.append("--bully")

    # --- timing ---
    for opt in req.timing:
        kind = opt.get("kind", "bool")
        if kind == "bool":
            if opt.get("state") and opt.get("flag"):
                flags.append(opt["flag"])
            continue
        if opt["name"] == "Scan Time" and not pillage:
            continue  # no -p for targeted attacks
        val = opt.get("state")
        if val in ("Off", "All"):
            continue
        _name_to_flag = {
            "Scan Time": "-p",           "WPS Timeout": "--wps-time",
            "WPA Timeout": "--wpat",     "Deauth Sec": "--wpadt",
            "PMKID Timeout": "--pmkid-timeout",
            "Num Deauths": "--num-deauths",
        }
        flag = _name_to_flag.get(opt["name"])
        if flag:
            flags.extend([flag, str(val)])

    # --- target filters ---
    for opt in req.filters:
        kind = opt.get("kind", "bool")
        if kind == "bool":
            if opt.get("state"):
                flags.append(opt.get("flag"))
        elif kind == "cycle":
            val = opt.get("state")
            if val in ("Off", "All"):
                continue
            arg = opt.get("flag")
            if arg:
                flags.extend([arg, str(val)])

    # --- interface options ---
    for opt in req.interface_opts:
        kind = opt.get("kind", "bool")
        if kind == "bool":
            if opt.get("state"):
                flags.append(opt["flag"])
        elif kind == "cycle":
            if opt["name"] == "Random MAC":
                val = opt.get("state")
                if val == "Full":
                    flags.append("-mac")
                elif val == "Vendor":
                    flags.append("--random-mac-vendor")
    return flags


def _append_filters(cmd, filters):
    """Emit only the filter-related flags from a config snapshot."""
    for opt in filters:
        kind = opt.get("kind", "bool")
        if kind == "bool" and opt.get("state"):
            cmd.append(opt["flag"])
        elif kind == "cycle":
            val = opt.get("state")
            if val in ("Off", "All"):
                continue
            arg = opt.get("flag")
            if arg:
                cmd.extend([arg, str(val)])


def _append_interface_opts(cmd, opts):
    """Emit only interface-level flags from a config snapshot."""
    for opt in opts:
        kind = opt.get("kind", "bool")
        if kind == "bool" and opt.get("state"):
            cmd.append(opt["flag"])
        elif kind == "cycle" and opt["name"] == "Random MAC":
            val = opt.get("state")
            if val == "Full":
                cmd.append("-mac")
            elif val == "Vendor":
                cmd.append("--random-mac-vendor")


from wifite.output import strip_ansi

_RAW_LOG = config.PROJECT_DIR + "/wifite_raw.log"


def _raw_log(line):
    """Append wifite's (ANSI-stripped) output for diagnosing parse issues."""
    try:
        with open(_RAW_LOG, "a") as f:
            f.write(strip_ansi(line)[:200] + "\n")
    except Exception:  # noqa: BLE001
        pass


def run_attack(iface, preset=None, progress_cb=None, status_cb=None,
               stop_flag=None):
    """Run an attack. Returns AttackResult."""
    try:
        open(_RAW_LOG, "w").close()      # fresh log each run
    except Exception:  # noqa: BLE001
        pass
    req = AttackRequest(
        interface=iface_mod.iface_name(iface),
        preset=preset,
        target_essid=config.Runtime.target_essid,
        target_bssid=config.Runtime.target_bssid,
        exclusions=list(config.Runtime.excluded_essids),
        attack_modes=[dict(x) for x in config.attack_modes],
        timing=[dict(x) for x in config.timing],
        filters=[dict(x) for x in config.filters],
        interface_opts=[dict(x) for x in config.interface_opts],
    )
    if preset and preset.get("name") == "resume":
        req = AttackRequest(interface=iface_mod.iface_name(iface),
                            resume_latest=True)

    cmd = _build_request_cmd(req)
    if not cmd:
        return AttackResult(ok=False, error="wifite not found")
    iface_name = req.interface
    start_time = time.time()

    # Don't pre-enable monitor mode (airmon-ng is slow and wifite does it on its
    # -i iface anyway). Just release the external adapter from NetworkManager so
    # wifite starts scanning fast without needing --kill (which would also drop
    # the internal wl0 used for uploads). The internal iface is left alone.
    _nm_set_managed(iface_name, False)
    config.Runtime.monitor_iface = iface_name
    try:
        # Binary + unbuffered: wifite redraws its live scan/attack line with
        # carriage returns and no newline, so readline() would block until the
        # scan ends. We read raw chunks and split on \r and \n ourselves.
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)

        parser = OutputParser()
        results = {"cracked": [], "handshakes": [], "failed": [], "cancelled": False}

        def _dispatch(events):
            for ev in events:
                t = ev.type.value
                if ev.type == EventType.CRACKED:
                    psk = ev.credential
                    results["cracked"].append({"essid": ev.essid, "psk": psk})
                    if status_cb:
                        status_cb({"type": t, "essid": ev.essid, "psk": psk})
                    record_cracked(ev.essid, psk)
                elif ev.type == EventType.HANDSHAKE:
                    results["handshakes"].append(ev.essid)
                    if status_cb:
                        status_cb({"type": t, "essid": ev.essid})
                elif ev.type == EventType.FAILED:
                    results["failed"].append(ev.essid)
                    if status_cb:
                        status_cb({"type": t, "essid": ev.essid,
                                   "detail": ev.detail})
                elif ev.type == EventType.TARGET:
                    if status_cb:
                        status_cb({"type": "attack", "essid": ev.essid,
                                   "bssid": ev.bssid,
                                   "current": ev.current, "total": ev.total})
                elif ev.type in (EventType.PHASE, EventType.CRACKING,
                                EventType.SCAN, EventType.PMKID,
                                EventType.SKIPPED, EventType.CLIENT,
                                EventType.DEAUTH):
                    if status_cb:
                        status_cb(ev.as_dict())
                elif ev.type == EventType.MESSAGE:
                    if status_cb:
                        status_cb({"type": "message", "text": ev.detail})

        fd = proc.stdout.fileno()
        buf = ""
        done = False
        while not done:
            if stop_flag and stop_flag.is_set():
                proc.terminate()
                time.sleep(1)
                try:
                    proc.kill()
                except Exception:
                    pass
                results["cancelled"] = True
                break
            # Wait up to 0.3s for data so stop_flag stays responsive on idle.
            try:
                ready, _, _ = select.select([fd], [], [], 0.3)
            except Exception:
                break
            if not ready:
                if progress_cb:
                    progress_cb(None, "elapsed %ds" % int(time.time() - start_time))
                continue
            try:
                chunk = os.read(fd, 4096)
            except Exception:
                break
            if not chunk:
                done = True
                if buf.strip():
                    _dispatch(parser.feed(buf))
                break
            buf += chunk.decode("utf-8", "replace")
            # Split on CR/LF; keep the trailing (possibly incomplete) segment.
            segments = re.split(r"[\r\n]", buf)
            buf = segments.pop()
            for seg in segments:
                if seg.strip():
                    _raw_log(seg)
                    _dispatch(parser.feed(seg))
            if progress_cb:
                progress_cb(None, "elapsed %ds" % int(time.time() - start_time))
        proc.wait()

    finally:
        iface_mod.disable_monitor(iface_name)   # restore managed mode
        _nm_set_managed(iface_name, True)        # hand the adapter back to NM

    return AttackResult(
        ok=not results["cancelled"],
        cancelled=results["cancelled"],
        exit_code=proc.returncode if 'proc' in dir() else None,
        cracked=results["cracked"],
        handshakes=results["handshakes"],
        failed=results["failed"],
        command=" ".join(cmd),
        monitor_iface=iface_name,
        elapsed=time.time() - start_time,
    )


def _nm_set_managed(iface_name, managed):
    """Tell NetworkManager to (not) manage an interface. Best-effort/no-op if
    nmcli is absent or the iface isn't NM-managed."""
    nmcli = shutil.which("nmcli") or "/usr/bin/nmcli"
    try:
        subprocess.run([nmcli, "device", "set", iface_name, "managed",
                        "yes" if managed else "no"],
                       capture_output=True, timeout=8)
    except Exception:  # noqa: BLE001
        pass

def record_cracked(essid, psk):
    """Persist a cracked credential to cracked.json (dedup by bssid/essid)."""
    if not essid:
        return
    entries = load_cracked()
    for e in entries:
        if e.get("essid") == essid and e.get("psk") == psk:
            return
    entries.append({"type": "WPA", "date": int(time.time()),
                    "essid": essid, "bssid": config.Runtime.current_bssid,
                    "pin": None, "psk": psk})
    save_cracked(entries)
