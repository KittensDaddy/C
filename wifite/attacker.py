# -*- coding: utf-8 -*-
"""Build and run wifite2 attacks.

Builds a command line from config + presets, spawns the process, streams its
output through OutputParser, and records cracked credentials to cracked.json.
"""
import subprocess
import threading
import time
import json
import os
import shutil

import config
from wifite import interface as iface_mod
from wifite.output import OutputParser
from cracked_store import load_cracked, save_cracked


def _which_wifite():
    for p in config.WIFITE_BIN_CANDIDATES:
        if os.path.exists(p):
            return p
    w = shutil.which("wifite")
    return w


def build_command(iface, preset=None):
    """Build a wifite argv list from config + optional preset args."""
    wifite = _which_wifite()
    if not wifite:
        return None
    name = iface_mod.iface_name(iface)
    cmd = ["sudo", wifite, "-i", name]

    if preset:
        cmd.extend(preset.get("args", []))
    else:
        cmd.extend(_config_flags())

    # Target / excludes
    if config.Runtime.target_bssid:
        cmd += ["-b", config.Runtime.target_bssid]
    if config.Runtime.target_essid:
        cmd += ["-e", config.Runtime.target_essid]
    for ex in config.Runtime.excluded_essids:
        cmd += ["-E", ex]
    return cmd


def _config_flags():
    """Translate toggle/cycle options into wifite argv."""
    flags = []

    # --- attack modes ---
    for opt in config.attack_modes:
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
    for opt in config.timing:
        kind = opt.get("kind", "bool")
        if kind == "bool":
            if opt.get("state") and opt.get("flag"):
                flags.append(opt["flag"])
            continue
        val = opt.get("state")
        if val in ("Off", "All"):
            continue
        _name_to_flag = {
            "Scan Time": "-p",    "WPS Timeout": "--wps-time",
            "WPA Timeout": "--wpat",    "Deauth Sec": "--wpadt",
            "PMKID Timeout": "--pmkid-timeout",
            "Num Deauths": "--num-deauths",
        }
        flag = _name_to_flag.get(opt["name"])
        if flag:
            flags.extend([flag, str(val)])

    # --- target filters ---
    for opt in config.filters:
        kind = opt.get("kind", "bool")
        if kind == "bool":
            if opt.get("state"):
                flags.append(opt["flag"])
        elif kind == "cycle":
            val = opt.get("state")
            if val in ("Off", "All"):
                continue
            arg = opt.get("flag")
            if arg:
                flags.extend([arg, str(val)])

    # --- interface options ---
    for opt in config.interface_opts:
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


def run_attack(iface, preset=None, progress_cb=None, status_cb=None,
               stop_flag=None):
    """Run the attack. Returns a dict summary.

    progress_cb(pct, label)  - called as attack progresses
    status_cb(event)         - raw parser events (dict)
    """
    cmd = build_command(iface, preset)
    if not cmd:
        return {"ok": False, "error": "wifite not found"}
    iface_name = iface_mod.iface_name(iface)

    # Ensure monitor mode for attacks
    if not iface_mod.enable_monitor(iface_name):
        # wifite can still try; not fatal
        pass

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, errors="replace")

    parser = OutputParser()
    results = {"cracked": [], "handshakes": [], "failed": []}
    start = time.time()

    def on_cracked(essid, psk):
        results["cracked"].append({"essid": essid, "psk": psk})
        if status_cb:
            status_cb({"type": "cracked", "essid": essid, "psk": psk})
        record_cracked(essid, psk)

    def on_handshake(essid, bssid):
        results["handshakes"].append(essid)
        if status_cb:
            status_cb({"type": "handshake", "essid": essid})

    def on_failed(essid):
        results["failed"].append(essid)
        if status_cb:
            status_cb({"type": "failed", "essid": essid})

    def on_phase(essid, phase):
        if status_cb:
            status_cb({"type": "phase", "essid": essid, "phase": phase})

    def on_message(text):
        if status_cb:
            status_cb({"type": "message", "text": text})

    def on_attack(essid, bssid):
        if status_cb:
            status_cb({"type": "attack", "essid": essid, "bssid": bssid})

    parser.handler = _Handler(on_cracked, on_handshake, on_failed,
                              on_phase, on_message, on_attack)

    done = False
    while not done:
        if stop_flag and stop_flag.is_set():
            proc.terminate()
            time.sleep(1)
            proc.kill()
            break
        try:
            line = proc.stdout.readline()
        except Exception:  # noqa: BLE001
            break
        if not line:
            done = True
            break
        parser.feed(line)
        elapsed = int(time.time() - start)
        if progress_cb:
            progress_cb(None, "elapsed %ds" % elapsed)
    proc.wait()

    # cleanup monitor mode
    iface_mod.disable_monitor(iface_name)

    results["ok"] = True
    results["command"] = " ".join(cmd)
    return results


class _Handler:
    def __init__(self, cracked, handshake, failed, phase, message, attack):
        self.on_cracked = cracked
        self.on_handshake = handshake
        self.on_failed = failed
        self.on_phase = phase
        self.on_message = message
        self.on_attack = attack

    def __getattr__(self, name):
        return lambda *a, **k: None


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
