# -*- coding: utf-8 -*-
"""Native attack orchestrator — replaces the old wifite2 driver.

Resolves a target list (multi-BSSID include/exclude + band filter), brings the
external adapter into monitor mode once, then runs WPS pixie and/or WPA capture
per target, emitting the same status_cb dict shapes ui/screens.py already knows.
Capture-only: handshakes are saved and recorded (psk=None); cracking is manual.
"""
import subprocess
import shutil
import time

import config
from attack import interface as iface_mod
from attack import scanner as sc
from attack import tools, wpa, wps
from attack.model import AttackRequest, AttackResult, AttackEvent, EventType
from cracked_store import load_cracked, save_cracked

try:
    from attack import pmkid as pmkid_mod
except Exception:  # noqa: BLE001
    pmkid_mod = None


# ---------------------------------------------------------------------------
# Request building
# ---------------------------------------------------------------------------
def _band_of(channel):
    try:
        ch = int(channel)
    except (ValueError, TypeError):
        return None
    return "2.4" if ch <= 14 else "5"


def _attacks_for(preset):
    """Which attacks to run + optional WPS-time override, from a preset or config."""
    if preset is not None:
        return set(preset.get("attacks", [])), preset.get("wps_time")
    attacks = set()
    if config.opt_bool(config.attack_modes, "WPA", True):
        attacks.add("wpa")
    if config.opt_bool(config.attack_modes, "WPS Pixie", True):
        attacks.add("wps")
    if config.opt_bool(config.attack_modes, "PMKID", False):
        attacks.add("pmkid")
    return attacks, None


# preset key -> (container name, option name). A preset is a self-contained
# recipe: any key present overrides that option on a COPY of the config for this
# run only (live config is never touched).
_PRESET_MAP = {
    "wps_time":    ("timing", "WPS Timeout"),
    "wpa_time":    ("timing", "WPA Timeout"),
    "num_deauths": ("timing", "Num Deauths"),
    "deauth_sec":  ("timing", "Deauth Sec"),
    "deauth":      ("attack_modes", "Deauth"),
    "tool":        ("attack_modes", "WPS Tool"),
    "ignore_locks": ("attack_modes", "Ignore Locks"),
    "band":        ("filters", "Band"),
    "max_targets": ("filters", "Max Targets"),
}


def _set_opt(container, name, value):
    for o in container:
        if o.get("name") == name:
            o["state"] = value
            return


def _apply_preset_overrides(preset, snapshots):
    """Stamp preset keys onto the copied snapshots. `snapshots` is a dict of
    container-name -> list-of-option-dicts."""
    if not preset:
        return
    for key, (container, opt_name) in _PRESET_MAP.items():
        if key in preset:
            _set_opt(snapshots[container], opt_name, preset[key])


def build_request(iface, preset=None):
    attacks, _ = _attacks_for(preset)
    snapshots = {
        "attack_modes": [dict(x) for x in config.attack_modes],
        "timing": [dict(x) for x in config.timing],
        "filters": [dict(x) for x in config.filters],
    }
    _apply_preset_overrides(preset, snapshots)
    band = config.opt_state(snapshots["filters"], "Band", "Both")
    bands = {"2.4": "2", "5": "5"}.get(band, "both")
    return AttackRequest(
        interface=iface_mod.iface_name(iface),
        preset=preset,
        target_bssids=list(config.Runtime.target_bssids),
        exclude_bssids=list(config.Runtime.excluded_bssids),
        exclude_essids=list(config.Runtime.excluded_essids),
        bands=bands,
        attacks=attacks,
        attack_modes=snapshots["attack_modes"],
        timing=snapshots["timing"],
        filters=snapshots["filters"],
    )


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------
def _as_target(net):
    return {"essid": net.get("essid"),
            "bssid": config._norm_bssid(net.get("bssid")) or net.get("bssid"),
            "channel": net.get("channel"), "signal": net.get("signal"),
            "wps": net.get("wps")}


def _filtered(nets, req, preset=None):
    cracked_bssids = set()
    if config.opt_bool(req.filters, "Ignore Cracked", False):
        cracked_bssids = {
            config._norm_bssid(e.get("bssid"))
            for e in load_cracked() if e.get("psk")}
    min_sig = config.opt_int(req.filters, "Min Signal", None)
    runtime_excl = {config._norm_bssid(b) for b in (req.exclude_bssids or [])}
    runtime_excl |= config.excluded_bssids()
    runtime_excl_essid = {(e or "").strip().upper()
                          for e in (req.exclude_essids or [])}
    out = []
    for n in nets:
        b = config._norm_bssid(n.get("bssid"))
        if not b:
            continue
        # Hardcoded + runtime excludes — match by normalized BSSID first.
        if b in runtime_excl or config.is_excluded_net(n):
            try:
                from attack import tools
                tools.log("skip excluded %s (%s)" % (
                    b, n.get("essid") or "?"))
            except Exception:  # noqa: BLE001
                pass
            continue
        essid = (n.get("essid") or "").strip()
        if essid.upper() in runtime_excl_essid:
            continue
        if b in cracked_bssids:
            continue
        if req.bands != "both" and _band_of(n.get("channel")) not in (None, req.bands):
            continue
        if min_sig is not None and (n.get("signal") or -100) < min_sig:
            continue
        out.append(n)
    return out


def _order_targets(nets, req):
    """When WPS is enabled, put known-WPS APs first; never drop unknowns."""
    if "wps" in req.attacks:
        return sc.sort_wps_first(nets)
    return sc.sort_by_signal(nets)


def _resolve_targets(iface_name, req, preset, status_cb, stop_flag):
    """Return a list of target dicts. Uses the multi-select include list when
    present, otherwise scans for `scan` seconds and takes everything found."""
    scan_map = {config._norm_bssid(n.get("bssid")): n
                for n in config.Runtime.last_scan}
    if req.target_bssids:
        chosen = [scan_map[b] for b in (
            config._norm_bssid(x) for x in req.target_bssids) if b in scan_map]
        return [_as_target(n) for n in _order_targets(
            _filtered(chosen, req, preset), req)]
    if config.Runtime.target_bssid:      # single picked target
        one = scan_map.get(config._norm_bssid(config.Runtime.target_bssid), {
            "essid": config.Runtime.target_essid,
            "bssid": config.Runtime.target_bssid,
            "channel": config.Runtime.target_channel})
        # Still honor hardcoded excludes for a manually picked target.
        if config.is_excluded_net(one):
            return []
        return [_as_target(one)]
    # Quick Attack / pillage: scan then take all.
    dur = int((preset or {}).get("scan", config.opt_int(req.timing, "Scan Time", 30)))
    if status_cb:
        status_cb({"type": "message", "text": "scanning %ds..." % dur})

    def _scan_progress(nets):
        if status_cb:
            found = [{"essid": n.get("essid") or n.get("bssid"),
                      "bssid": n.get("bssid"),
                      "signal": n.get("signal")} for n in nets]
            status_cb({"type": "scan", "targets": len(nets), "found": found})

    nets = sc.scan(iface_name, duration=dur, progress_cb=_scan_progress,
                   stop_flag=stop_flag)
    config.Runtime.last_scan = nets
    nets = _order_targets(_filtered(nets, req, preset), req)
    cap = config.opt_int(req.filters, "Max Targets", None)
    if cap:
        nets = nets[:cap]
    return [_as_target(n) for n in nets]


# ---------------------------------------------------------------------------
# Plan preview (for ui CommandPreview)
# ---------------------------------------------------------------------------
def plan_lines(req, n_targets):
    """Pre-flight plan — the REAL values for this run, read straight from the
    request (which already has the launching preset's overrides applied). Each
    line reflects only the attacks that will actually run."""
    band = {"2": "2.4GHz", "5": "5GHz", "both": "2.4+5GHz"}[req.bands]
    lines = ["iface: %s" % req.interface, "band: %s" % band]
    if "wps" in req.attacks:
        lines.append("WPS pixie %s %ss" % (
            config.opt_state(req.attack_modes, "WPS Tool", "reaver"),
            config.opt_int(req.timing, "WPS Timeout", 180)))
    if "pmkid" in req.attacks:
        lines.append("PMKID %ss (clientless)" %
                     config.opt_int(req.timing, "WPA Timeout", 300))
    if "wpa" in req.attacks:
        wpa_t = config.opt_int(req.timing, "WPA Timeout", 300)
        if config.opt_bool(req.attack_modes, "Deauth", True):
            lines.append("WPA %ss deauth x%d" % (
                wpa_t, config.opt_int(req.timing, "Num Deauths", 5)))
        else:
            lines.append("WPA %ss passive" % wpa_t)
    if not (req.attacks & {"wps", "wpa", "pmkid"}):
        lines.append("survey only (no attack)")
    lines.append("targets: %s" % (n_targets if n_targets else "scan"))
    return lines


# ---------------------------------------------------------------------------
# NetworkManager handoff (same behaviour as the old attacker.py)
# ---------------------------------------------------------------------------
def _nm_set_managed(iface_name, managed):
    nmcli = shutil.which("nmcli") or "/usr/bin/nmcli"
    try:
        subprocess.run([nmcli, "device", "set", iface_name, "managed",
                        "yes" if managed else "no"],
                       capture_output=True, timeout=8)
    except Exception:  # noqa: BLE001
        pass


def _record_capture(essid, bssid, cap=None, psk=None, typ="WPA", pin=None,
                    channel=None):
    """Persist a capture/crack to cracked.json (dedup by bssid)."""
    entries = load_cracked()
    for e in entries:
        if e.get("bssid") == bssid:
            if cap:
                e["cap"] = cap
            if psk and not e.get("psk"):
                e["psk"] = psk
            if pin and not e.get("pin"):
                e["pin"] = pin
            if channel and not e.get("channel"):
                e["channel"] = channel
            save_cracked(entries)
            return
    entries.append({"type": typ, "date": int(time.time()), "essid": essid,
                    "bssid": bssid, "pin": pin, "psk": psk, "cap": cap,
                    "channel": channel})
    save_cracked(entries)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def run(iface, preset=None, progress_cb=None, status_cb=None, stop_flag=None):
    req = build_request(iface, preset=preset)
    iface_name = req.interface
    start = time.time()

    def emit(ev):
        if not status_cb:
            return
        if ev.type == EventType.TARGET:
            status_cb({"type": "attack", "essid": ev.essid, "bssid": ev.bssid,
                       "current": ev.current, "total": ev.total})
        else:
            status_cb(ev.as_dict())

    results = {"cracked": [], "handshakes": [], "failed": [], "cancelled": False}
    _nm_set_managed(iface_name, False)

    try:
        targets = _resolve_targets(iface_name, req, preset, status_cb, stop_flag)
        if not targets:
            return AttackResult(ok=False, error="no targets",
                                monitor_iface=iface_name,
                                elapsed=time.time() - start)

        mon = iface_mod.enable_monitor(iface_name)
        if not mon:
            return AttackResult(ok=False, error="monitor mode failed",
                                monitor_iface=iface_name,
                                elapsed=time.time() - start)
        config.Runtime.monitor_iface = mon

        total = len(targets)
        for i, t in enumerate(targets, 1):
            if stop_flag and stop_flag.is_set():
                results["cancelled"] = True
                break
            emit(AttackEvent(EventType.TARGET, essid=t.get("essid"),
                             bssid=t.get("bssid"), current=i, total=total))

            if "wps" in req.attacks:
                r = wps.pixie(mon, t, req, emit, stop_flag)
                if r.get("cancelled"):
                    results["cancelled"] = True
                    break
                if r.get("ok"):
                    cred = r.get("psk") or r.get("pin")
                    results["cracked"].append({"essid": r["essid"], "psk": cred})
                    _record_capture(r["essid"], r["bssid"], psk=r.get("psk"),
                                    pin=r.get("pin"), typ="WPS",
                                    channel=t.get("channel"))
                    if r.get("psk"):
                        continue  # got the actual PSK — done with this target
                    # PIN-only (no PSK): keep the PIN but still try to capture a
                    # usable handshake below, since a PIN isn't the password.

            if "pmkid" in req.attacks and pmkid_mod:
                r = pmkid_mod.capture(mon, t, req, emit, stop_flag)
                if r.get("ok"):
                    results["handshakes"].append({"essid": r["essid"],
                                                  "cap": r.get("hash")})
                    _record_capture(r["essid"], r["bssid"], cap=r.get("hash"),
                                    typ="PMKID", channel=t.get("channel"))
                    continue

            if "wpa" in req.attacks:
                r = wpa.capture(mon, t, req, emit, stop_flag)
                if r.get("cancelled"):
                    results["cancelled"] = True
                    break
                if r.get("ok"):
                    results["handshakes"].append({"essid": r["essid"],
                                                  "cap": r.get("cap")})
                    _record_capture(r["essid"], r["bssid"], cap=r.get("cap"),
                                    channel=t.get("channel") or r.get("channel"))
                else:
                    results["failed"].append(r.get("essid"))
    finally:
        iface_mod.disable_monitor(config.Runtime.monitor_iface or iface_name)
        _nm_set_managed(iface_name, True)
        config.Runtime.monitor_iface = None
        # Restart NM so the internal wl0 reconnects to its home network (restores
        # SSH — monitor-mode churn on the external card disrupts NM's wl0).
        try:
            from network import connect as _net_connect
            _net_connect.restore_network()
        except Exception:  # noqa: BLE001
            pass

    return AttackResult(
        ok=not results["cancelled"],
        cancelled=results["cancelled"],
        cracked=results["cracked"],
        handshakes=results["handshakes"],
        failed=results["failed"],
        command=" · ".join(plan_lines(req, len(targets))),
        monitor_iface=iface_name,
        elapsed=time.time() - start,
    )
