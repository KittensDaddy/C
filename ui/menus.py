# -*- coding: utf-8 -*-
"""Menu navigation. Event-driven; buttons feed a queue via hardware.buttons.

Screen functions render then loop waiting for events. key1 = back,
key2 = toggle/stealth, press = activate.
"""
import time
import threading
import queue
from PIL import ImageFont

import config
from ui import theme
from ui.screens import (display, font, status_bar, header, box,
                        AttackStatus, ProgressView, CommandPreview, render_splash)
from hardware import battery
from wifite import interface as iface_mod, scanner as sc, attacker
from cracked_store import load_cracked, load_settings, save_settings
from network import connect as net_connect, tailscale, upload as net_upload

_evq = queue.Queue()


def set_button_manager(bm):
    bm.callback = _on_button


def _on_button(ev):
    _evq.put(ev)


def wait_event(timeout=0.2):
    try:
        return _evq.get(timeout=timeout)
    except queue.Empty:
        return None


def render_list(d, labels, active, start_idx=0):
    """Render up to OPTIONS_PER_PAGE labels, highlight active."""
    n = len(labels)
    page = config.OPTIONS_PER_PAGE
    start = max(0, active - page + 1)
    end = min(n, start + page)
    y = 15
    for i in range(start, end):
        label = labels[i]
        if i == active:
            theme.shadowed(d, "> " + label, (3, y),
                           font(), color=theme.highlight_color())
        else:
            theme.shadowed(d, "  " + label, (3, y), font())
        y += 10
    status_bar(d)


def _move(active, n, ev):
    if ev["type"] == "up":
        return (active - 1) % n
    if ev["type"] == "down":
        return (active + 1) % n
    return active


def _persist():
    """Save theme + exclusions to settings.json (survives reboot)."""
    s = load_settings()
    s["theme"] = theme.theme_index()
    s["exclusions"] = list(config.Runtime.excluded_essids)
    save_settings(s)


# --------------------------------------------------------------------------
# Toggle helpers
# --------------------------------------------------------------------------
def toggle(opt):
    kind = opt.get("kind", "bool")
    if kind == "bool":
        opt["state"] = not opt.get("state", False)
    elif kind == "cycle":
        vals = opt.get("values", [])
        if vals:
            cur = opt.get("state")
            idx = vals.index(cur) if cur in vals else 0
            opt["state"] = vals[(idx + 1) % len(vals)]


# --------------------------------------------------------------------------
# Main menu
# --------------------------------------------------------------------------
def main_menu():
    while True:
        d = display.begin()
        box(d, "MAIN")
        labels = ["Scan & Attack", "Quick Attack", "Config", "Cracked",
                  "Connect", "Upload", "Resume", "SysCheck", "Theme",
                  "Stealth"]
        active = 0
        render_list(d, labels, active)
        display.show()

        while True:
            ev = wait_event()
            if not ev:
                continue
            active = _move(active, len(labels), ev)
            if ev["type"] == "down" or ev["type"] == "up":
                d = display.begin()
                box(d, "MAIN")
                render_list(d, labels, active)
                display.show()
            elif ev["type"] == "press":
                choice = labels[active]
                if choice == "Scan & Attack":
                    scan_attack()
                elif choice == "Quick Attack":
                    quick_attack()
                elif choice == "Config":
                    config_menu()
                elif choice == "Cracked":
                    cracked_viewer()
                elif choice == "Connect":
                    connect_menu()
                elif choice == "Upload":
                    do_upload()
                elif choice == "Resume":
                    resume_menu()
                elif choice == "SysCheck":
                    syscheck()
                elif choice == "Theme":
                    theme_menu()
                elif choice == "Stealth":
                    stealth()
                d = display.begin()
                box(d, "MAIN")
                render_list(d, labels, active)
                display.show()
                break
            elif ev["type"] == "key2":
                stealth()


# --------------------------------------------------------------------------
# Scan & Attack
# --------------------------------------------------------------------------
def scan_attack():
    iface = iface_mod.pick_external(iface_mod.get_interfaces())
    if not iface:
        status("No external interface")
        time.sleep(1.5)
        return
    config.Runtime.selected_interface = iface

    pv = ProgressView("SCAN")
    pv.push("Using %s" % iface[0])

    duration = _scan_time_seconds()
    nets = sc.scan(iface[0], duration=duration,
                   progress_cb=lambda n: pv.push("found %d nets" % n),
                   stop_flag=None)
    nets = sc.sort_by_signal(nets)

    if not nets:
        pv.push("ERR no networks found")
        pv.render()
        time.sleep(2)
        return

    # pick target
    labels = [n["essid"] for n in nets]
    active = 0
    d = display.begin()
    box(d, "TARGET")
    render_list(d, labels, active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(labels), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "TARGET")
            render_list(d, labels, active)
            display.show()
        elif ev["type"] == "press":
            target = nets[active]
            config.Runtime.target_essid = target["essid"]
            config.Runtime.target_bssid = target["bssid"]
            config.Runtime.current_bssid = target["bssid"]
            choose_mode(target["essid"])
            return
        elif ev["type"] == "key1":
            return


def choose_mode(essid):
    labels = ["Run: Current Config", "Run: Preset..."]
    active = 0
    d = display.begin()
    box(d, "MODE")
    render_list(d, labels, active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(labels), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "MODE")
            render_list(d, labels, active)
            display.show()
        elif ev["type"] == "press":
            if active == 0:
                run_and_show(None)
            else:
                quick_attack()
            return
        elif ev["type"] == "key1":
            return


# --------------------------------------------------------------------------
# Quick Attack (presets)
# --------------------------------------------------------------------------
def quick_attack():
    labels = [p["name"] for p in config.presets]
    active = 0
    d = display.begin()
    box(d, "PRESET")
    render_list(d, labels, active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(labels), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "PRESET")
            render_list(d, labels, active)
            display.show()
        elif ev["type"] == "press":
            run_and_show(config.presets[active])
            return
        elif ev["type"] == "key1":
            return


def run_and_show(preset):
    iface = iface_mod.pick_external(iface_mod.get_interfaces())
    if not iface:
        status("No external interface")
        time.sleep(1.5)
        return
    name = iface_mod.iface_name(iface)
    drv = iface[1] if isinstance(iface, tuple) else ""

    # Pre-flight: type out the exact wifite command for a recheck. KEY1 aborts,
    # any other press runs immediately; otherwise it auto-advances at 0.5s/line.
    cmd = attacker.build_command(iface, preset=preset)
    if cmd:
        prev = CommandPreview(cmd, mode=(preset.get("name") if preset else "custom"),
                              iface=name, driver=drv)
        skipped = False
        for i in range(1, len(prev) + 1):
            prev.render(i)
            e = wait_event(0.5)
            if e and e["type"] == "key1":
                return                      # cancel the whole attack
            if e and e["type"] in ("press", "key3"):
                skipped = True
                break
        prev.render(len(prev))
        if not skipped:
            # brief hold so the finished command is readable before launch
            for _ in range(4):
                prev.render(len(prev))
                e = wait_event(0.25)
                if e and e["type"] == "key1":
                    return
                if e and e["type"] in ("press", "key3"):
                    break

    st = AttackStatus("ATTACK")
    st.set_iface(name, drv)
    st.render()
    stop = threading.Event()

    def progress(pct, label):
        st.label = label
        st.render()

    def status_ev(ev):
        st.handle_event(ev)
        st.render()

    def stop_check():
        while True:
            e = wait_event(0.3)
            if e and e["type"] == "key1":
                stop.set()
            if e and e["type"] == "key3":
                stop.set()
            if stop.is_set():
                break

    monitor = threading.Thread(target=stop_check, daemon=True)
    monitor.start()

    res = attacker.run_attack(iface, preset=preset,
                              progress_cb=progress, status_cb=status_ev,
                              stop_flag=stop)

    # summary
    pv = ProgressView("DONE")
    pv.push("OK attack finished" if res.ok else "ERR %s" %
            (res.error or "failed"))
    for c in res.cracked[-4:]:
        pv.push("OK %s=%s" % (c["essid"], c["psk"]))
    pv.render()
    time.sleep(3)


# --------------------------------------------------------------------------
# Config menu + sections
# --------------------------------------------------------------------------
def config_menu():
    labels = ["Attack Modes", "Timing", "Target Filters", "Interface",
              "Exclude SSIDs", "Save as Preset"]
    active = 0
    d = display.begin()
    box(d, "CONFIG")
    render_list(d, labels, active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(labels), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "CONFIG")
            render_list(d, labels, active)
            display.show()
        elif ev["type"] == "press":
            if labels[active] == "Attack Modes":
                toggle_section("ATTACK MODES", config.attack_modes)
            elif labels[active] == "Timing":
                toggle_section("TIMING", config.timing)
            elif labels[active] == "Target Filters":
                toggle_section("FILTERS", config.filters)
            elif labels[active] == "Interface":
                toggle_section("INTERFACE", config.interface_opts)
            elif labels[active] == "Exclude SSIDs":
                exclude_menu()
            elif labels[active] == "Save as Preset":
                save_preset()
            d = display.begin()
            box(d, "CONFIG")
            render_list(d, labels, active)
            display.show()
            break
        elif ev["type"] == "key1":
            return


def toggle_section(title, opts):
    active = 0
    n = len(opts)
    page = config.OPTIONS_PER_PAGE
    # fixed column where toggle dots / values are drawn (right aligned)
    val_x = config.WIDTH - 14

    def _render():
        d = display.begin()
        box(d, title)
        start = max(0, active - page + 1)
        end = min(n, start + page)
        y = 16
        for i in range(start, end):
            opt = opts[i]
            kind = opt.get("kind", "bool")
            active_row = (i == active)
            if active_row:
                theme.shadowed(d, "> " + opt["name"], (2, y),
                               font(), color=theme.highlight_color())
            else:
                theme.shadowed(d, "  " + opt["name"], (2, y), font())
            # toggle dot or value on the right
            if kind == "bool":
                on = bool(opt.get("state"))
                theme.circle(d, val_x, y + 4, 3,
                             theme.accent_color() if on else (255, 0, 0))
            else:
                theme.shadowed(d, opt.get("state", ""), (val_x - 26, y),
                               font(), color=theme.highlight_color() if active_row
                               else theme.palette()["text"])
            y += 10
        status_bar(d)
        display.show()

    _render()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, n, ev)
        if ev["type"] in ("up", "down"):
            _render()
        elif ev["type"] in ("press", "key2"):
            toggle(opts[active])
            _render()
        elif ev["type"] == "key1":
            return


def save_preset():
    pv = ProgressView("SAVE")
    pv.push("Saved current config")
    # derive args from config
    pv.push("as: 'Custom %d'" % (len(config.presets)))
    pv.render()
    time.sleep(1.5)
    config.presets.append({
        "name": "Custom %d" % len(config.presets),
        "desc": "saved config",
        "args": attacker._config_flags(),
    })


def _scan_time_seconds():
    for o in config.timing:
        if o["name"] == "Scan Time":
            try:
                return int(o["state"])
            except ValueError:
                return 20
    return 20


# --------------------------------------------------------------------------
# Exclude ESSIDs
# --------------------------------------------------------------------------
def exclude_menu():
    iface = iface_mod.pick_external(iface_mod.get_interfaces())
    if not iface:
        status("No external interface")
        time.sleep(1.5)
        return
    pv = ProgressView("SCAN")
    pv.push("scanning...")
    nets = sc.scan(iface[0], duration=_scan_time_seconds(),
                   progress_cb=lambda n: pv.push("found %d" % n))
    nets = sc.sort_by_signal(nets)
    if not nets:
        pv.push("ERR no networks")
        pv.render()
        time.sleep(2)
        return
    labels = [n["essid"] for n in nets]
    active = 0
    def _labels():
        out = []
        for n in nets:
            marker = "!" if n["essid"] in config.Runtime.excluded_essids else " "
            out.append("%s%s" % (marker, n["essid"]))
        return out
    d = display.begin()
    box(d, "EXCLUDE")
    render_list(d, _labels(), active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(labels), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "EXCLUDE")
            render_list(d, _labels(), active)
            display.show()
        elif ev["type"] == "press":
            essid = labels[active]
            if essid in config.Runtime.excluded_essids:
                config.Runtime.excluded_essids.remove(essid)
            else:
                config.Runtime.excluded_essids.append(essid)
            _persist()
            d = display.begin()
            box(d, "EXCLUDE")
            render_list(d, _labels(), active)
            display.show()
        elif ev["type"] == "key1":
            return


# --------------------------------------------------------------------------
# Cracked viewer
# --------------------------------------------------------------------------
def cracked_viewer():
    entries = load_cracked()
    if not entries:
        status("No cracked networks yet")
        time.sleep(1.5)
        return
    active = 0
    def labels():
        return ["%s [%s]" % (e["essid"], e.get("psk") or "no-psk")
                for e in entries]
    d = display.begin()
    box(d, "CRACKED")
    render_list(d, labels(), active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(entries), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "CRACKED")
            render_list(d, labels(), active)
            display.show()
        elif ev["type"] == "press":
            psk = entries[active].get("psk")
            if psk:
                config.Runtime.target_essid = entries[active]["essid"]
                connect_to_ssid(entries[active]["essid"], psk)
            else:
                status("no password for this one")
                time.sleep(1)
            d = display.begin()
            box(d, "CRACKED")
            render_list(d, labels(), active)
            display.show()
        elif ev["type"] == "key1":
            return


# --------------------------------------------------------------------------
# Connect
# --------------------------------------------------------------------------
def connect_menu():
    entries = load_cracked()
    with_psk = [e for e in entries if e.get("psk")]
    if not with_psk:
        status("No cracked passwords to connect with")
        time.sleep(1.5)
        return
    active = 0
    def labels():
        return [e["essid"] for e in with_psk]
    d = display.begin()
    box(d, "CONNECT")
    render_list(d, labels(), active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(with_psk), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "CONNECT")
            render_list(d, labels(), active)
            display.show()
        elif ev["type"] == "press":
            e = with_psk[active]
            connect_to_ssid(e["essid"], e["psk"])
            return
        elif ev["type"] == "key1":
            return


def connect_to_ssid(ssid, psk):
    pv = ProgressView("CONNECT")
    ok, iface, err = net_connect.connect(ssid, psk,
                                         progress_cb=pv.push)
    if ok:
        pv.push("OK connected: %s" % iface)
        # bring up tailscale
        if tailscale.up():
            pv.push("OK tailscale up")
            pv.push("server reachable: %s" %
                    ("yes" if tailscale.ping_server() else "no"))
        else:
            pv.push("TS needs auth / down")
    else:
        pv.push("ERR %s" % (err or "failed"))
    pv.render()
    time.sleep(3)


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------
def do_upload():
    pv = ProgressView("UPLOAD")
    res = net_upload.upload(progress_cb=pv.push)
    if res.get("ok"):
        pv.push("OK uploaded %d entries" % res["uploaded"])
    else:
        pv.push("ERR %s" % res.get("error", "failed"))
    pv.render()
    time.sleep(3)


# --------------------------------------------------------------------------
# Resume
# --------------------------------------------------------------------------
def resume_menu():
    labels = ["Resume Latest", "Clean Sessions", "Back"]
    active = 0
    d = display.begin()
    box(d, "RESUME")
    render_list(d, labels, active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(labels), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "RESUME")
            render_list(d, labels, active)
            display.show()
        elif ev["type"] == "press":
            if labels[active] == "Resume Latest":
                iface = iface_mod.pick_external(iface_mod.get_interfaces())
                if iface:
                    run_and_show({"name": "resume", "args": ["--resume-latest"]})
                return
            elif labels[active] == "Clean Sessions":
                status("cleaned")
                time.sleep(1)
            return
        elif ev["type"] == "key1":
            return


# --------------------------------------------------------------------------
# Theme picker
# --------------------------------------------------------------------------
def theme_menu():
    names = [t["name"] for t in config.COLOR_THEMES]
    active = theme.theme_index()
    d = display.begin()
    box(d, "THEME")
    render_list(d, names, active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(names), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "THEME")
            render_list(d, names, active)
            display.show()
        elif ev["type"] == "press":
            theme.set_theme(active)
            _persist()
            d = display.begin()
            box(d, "THEME")
            render_list(d, names, active)
            display.show()
        elif ev["type"] == "key1":
            return


# --------------------------------------------------------------------------
# SysCheck
# --------------------------------------------------------------------------
def syscheck():
    pv = ProgressView("SYSCHECK")
    from wifite.attacker import _which_wifite
    pv.push("wifite: %s" % ("ok" if _which_wifite() else "MISSING"))
    pv.push("lgpio: %s" % ("ok" if _import_ok("lgpio") else "MISSING"))
    pv.push("tailscale: %s" % tailscale.status())
    ifaces = iface_mod.get_interfaces()
    pv.push("interfaces: %d" % len(ifaces))
    for name, drv in ifaces[:2]:
        pv.push("  %s (%s)" % (name, drv))
    pv.render()
    time.sleep(3)


def _import_ok(mod):
    try:
        __import__(mod)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Stealth (simple pong)
# --------------------------------------------------------------------------
def stealth():
    d = display.begin()
    box(d, "STEALTH")
    # minimal: show a message, exit on any button
    theme.shadowed(d, "covert", (10, config.HEIGHT // 2), font(),
                   color=theme.accent_color())
    display.show()
    while True:
        ev = wait_event(1.0)
        if ev:
            return


def status(msg):
    d = display.begin()
    box(d, "INFO")
    theme.shadowed(d, msg[:config.MAX_CHARS_PER_LINE], (3, 40), font())
    display.show()
