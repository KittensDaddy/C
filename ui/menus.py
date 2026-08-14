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
from attack import interface as iface_mod, scanner as sc, orchestrator
from attack import crack as crack_mod, tools as atk_tools
from cracked_store import load_cracked, load_settings, save_settings
from network import connect as net_connect, tailscale, upload as net_upload

_evq = queue.Queue()


def set_button_manager(bm):
    bm.callback = _on_button
    start_animator()


# --------------------------------------------------------------------------
# Background animator: keeps the status-bar cat running even when a screen is
# idle (menus only repaint on input). It overlays just the bottom strip, so it
# works on top of whatever screen is currently shown.
# --------------------------------------------------------------------------
_anim_enabled = True
_anim_thread = None


def set_animated(on):
    """Enable/disable the status-bar animator (off for full-screen views that
    draw their own bottom, e.g. the command preview)."""
    global _anim_enabled
    _anim_enabled = on


def _paint_statusbar(d):
    # Clear a strip tall enough to cover the cat's ear/tail tips (which reach a
    # px or two above the bar) so no pixels are left behind between frames.
    h, w = config.HEIGHT, config.WIDTH
    d.rectangle((0, h - 14, w, h), fill=theme.background())
    status_bar(d)


def _animator_loop():
    while True:
        if _anim_enabled:
            try:
                # Partial refresh: only the bottom status strip changes, so push
                # just those rows instead of the whole frame (avoids SPI lag).
                display.overlay(_paint_statusbar,
                                region=(config.HEIGHT - 14, config.HEIGHT))
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.16)          # ~6 fps cat (each frame is a full-frame SPI push)


def start_animator():
    global _anim_thread
    if _anim_thread is None:
        _anim_thread = threading.Thread(target=_animator_loop, daemon=True)
        _anim_thread.start()


def _on_button(ev):
    # Joystick navigates between menus: left = back (like KEY1), right =
    # enter/select (like the joystick press). Up/down scroll within a list.
    if ev.get("type") == "left":
        ev["type"] = "key1"
    elif ev.get("type") == "right":
        ev["type"] = "press"
    _evq.put(ev)


def wait_event(timeout=0.2):
    try:
        return _evq.get(timeout=timeout)
    except queue.Empty:
        return None


def flush_events():
    """Drop any queued button events (e.g. the press that opened this screen)."""
    try:
        while True:
            _evq.get_nowait()
    except queue.Empty:
        pass


VISIBLE_ROWS = 7          # FlipHUD rows that fit between header and status bar


def _scrollbar(d, n, start, per):
    """Right-edge scroll thumb when the list is longer than one page."""
    if n <= per:
        return
    W, top, bot = config.WIDTH, 16, config.HEIGHT - 16
    track = bot - top
    thumb = max(8, track * per // n)
    ty = top + (track - thumb) * start // max(1, n - per)
    d.rectangle((W - 3, top, W - 2, bot), fill=theme.rule_color())
    d.rectangle((W - 4, ty, W - 1, ty + thumb), fill=theme.accent_color())


def render_list(d, labels, active, start_idx=0):
    """FlipHUD menu list: selection bar + optional scrollbar."""
    n = len(labels)
    per = VISIBLE_ROWS
    start = max(0, min(active - per + 1, n - per)) if n > per else 0
    end = min(n, start + per)
    w = config.WIDTH - (10 if n > per else 6)
    y = 16
    for i in range(start, end):
        y = theme.list_row(d, 3, y, w, labels[i], i == active, font())
    _scrollbar(d, n, start, per)
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
    s["exclusions_bssid"] = list(config.Runtime.excluded_bssids)
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
        labels = ["Scan & Attack", "Quick Attack", "Crack", "Cracked",
                  "Connect", "Upload", "Config", "SysCheck"]
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
                    # Main-menu Quick Attack pillages all — clear any target
                    # left over from a previous Scan & Attack selection.
                    config.Runtime.target_essid = None
                    config.Runtime.target_bssid = None
                    config.Runtime.target_channel = None
                    config.Runtime.target_bssids = []
                    quick_attack()
                elif choice == "Crack":
                    crack_menu()
                elif choice == "Config":
                    config_menu()
                elif choice == "Cracked":
                    cracked_viewer()
                elif choice == "Connect":
                    connect_menu()
                elif choice == "Upload":
                    do_upload()
                elif choice == "SysCheck":
                    syscheck()
                d = display.begin()
                box(d, "MAIN")
                render_list(d, labels, active)
                display.show()
                break


# --------------------------------------------------------------------------
# Scan & Attack
# --------------------------------------------------------------------------
def _scan_with_progress(iface_name, driver="", title="SCAN"):
    """Run a scan in the background and render a live countdown + found count.
    Shows the interface + driver so you can confirm the external adapter is used.
    Returns the sorted network list."""
    duration = config.INTERACTIVE_SCAN_SECONDS
    state = {"n": 0, "done": False, "nets": []}

    def worker():
        nets = sc.scan(iface_name, duration=duration,
                       progress_cb=lambda ns: state.__setitem__("n", len(ns)))
        state["nets"] = sc.sort_by_signal(nets)
        state["done"] = True

    start = time.time()
    threading.Thread(target=worker, daemon=True).start()
    while not state["done"]:
        left = max(0.0, duration - (time.time() - start))
        d = display.begin()
        box(d, title)
        theme.shadowed(d, ("%s %s" % (iface_name, driver)).strip()[:20],
                       (3, 16), font(), color=theme.highlight_color())
        theme.shadowed(d, "found %d nets" % state["n"], (3, 28), font(),
                       color=theme.accent_color())
        theme.progress_bar(d, 3, 44, config.WIDTH - 6, 5,
                           int(100 * left / duration))
        theme.shadowed(d, "%ds left" % int(left + 0.5), (3, 52), font())
        display.show()
        time.sleep(0.1)
    return state["nets"]


def scan_attack():
    iface = iface_mod.pick_external(iface_mod.get_interfaces())
    if not iface:
        status("No external interface")
        time.sleep(1.5)
        return
    config.Runtime.selected_interface = iface

    nets = _scan_with_progress(iface[0],
                               iface[1] if isinstance(iface, tuple) else "", "SCAN")

    if not nets:
        ev = ProgressView("SCAN")
        ev.push("ERR no networks found")
        ev.render()
        time.sleep(2)
        return

    # Remember the scan so the orchestrator can look up channel/essid by BSSID.
    config.Runtime.last_scan = nets

    # Multi-select include: press toggles a target; row 0 launches the attack on
    # everything selected (a '+' marker flags each chosen AP).
    selected = set()

    def _labels():
        rows = ["> Attack (%d)" % len(selected)]
        for n in nets:
            mark = "+" if n["bssid"] in selected else " "
            rows.append("%s%s" % (mark, n["essid"]))
        return rows

    active = 0
    n_rows = len(nets) + 1

    def _render():
        d = display.begin()
        box(d, "TARGET")
        render_list(d, _labels(), active)
        display.show()

    _render()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, n_rows, ev)
        if ev["type"] in ("up", "down"):
            _render()
        elif ev["type"] == "press":
            if active == 0:
                if not selected:
                    status("select a target first")
                    time.sleep(1)
                    _render()
                    continue
                config.Runtime.target_bssids = list(selected)
                config.Runtime.target_bssid = None      # use the multi list
                config.Runtime.target_essid = None
                choose_mode("%d targets" % len(selected))
                return
            n = nets[active - 1]
            if n["bssid"] in selected:
                selected.discard(n["bssid"])
            else:
                selected.add(n["bssid"])
            _render()
        elif ev["type"] == "key1":
            config.Runtime.target_bssids = []
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

    # Pre-flight: type out the native attack plan for a recheck. KEY1 aborts,
    # any other press runs immediately; otherwise it auto-advances at 0.5s/line.
    req = orchestrator.build_request(iface, preset=preset)
    n_targets = len(config.Runtime.target_bssids) or None
    lines = orchestrator.plan_lines(req, n_targets if n_targets is not None else 0)
    if lines:
        flush_events()          # drop the keypress that launched this attack
        set_animated(False)     # preview owns its bottom row (no status bar)
        prev = CommandPreview(lines, mode=(preset.get("name") if preset else "custom"),
                              iface=name, driver=drv)
        skipped = False
        for i in range(1, len(prev) + 1):
            prev.render(i)
            e = wait_event(0.5)
            if e and e["type"] == "key1":
                set_animated(True)
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
                    set_animated(True)
                    return
                if e and e["type"] in ("press", "key3"):
                    break
    # Keep the background animator OFF during the attack: the repaint ticker below
    # already redraws the whole screen (cat included) ~2x/sec, so leaving the 6 fps
    # animator running would double the full-frame SPI pushes and cause lag.
    set_animated(False)

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

    # Repaint ~2x/sec so the countdown ticks and the deauth/spinner animate even
    # between the engine's sparser heartbeat events.
    done = threading.Event()

    def ticker():
        while not done.is_set():
            if st.started:
                st.render()
            done.wait(0.5)

    threading.Thread(target=ticker, daemon=True).start()

    try:
        res = orchestrator.run(iface, preset=preset,
                               progress_cb=progress, status_cb=status_ev,
                               stop_flag=stop)
    finally:
        done.set()
        set_animated(True)      # hand the cat animator back to the menus

    # summary
    pv = ProgressView("DONE")
    pv.push("OK attack finished" if res.ok else "ERR %s" %
            (res.error or "failed"))
    for c in res.cracked[-3:]:
        pv.push("KEY %s=%s" % (c["essid"], c["psk"]))
    for h in res.handshakes[-3:]:
        pv.push("HS  %s (Crack menu)" % h.get("essid"))
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
    per = VISIBLE_ROWS

    def _render():
        d = display.begin()
        box(d, title)
        start = max(0, min(active - per + 1, n - per)) if n > per else 0
        end = min(n, start + per)
        w = config.WIDTH - (10 if n > per else 6)
        y = 16
        for i in range(start, end):
            opt = opts[i]
            kind = opt.get("kind", "bool")
            sel = (i == active)
            val = str(opt.get("state", "")) if kind == "cycle" else None
            theme.list_row(d, 3, y, w, opt["name"], sel, font(), value=val)
            if kind == "bool":                    # ON/OFF dot at the right edge
                on = bool(opt.get("state"))
                cx, cy = 3 + w - 9, y + 6
                fg = theme.background() if sel else \
                    (theme.accent_color() if on else theme.dim_color())
                if on:
                    theme.circle(d, cx, cy, 3, fg)
                else:
                    d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), outline=fg)
            y += 14
        _scrollbar(d, n, start, per)
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
    attacks = []
    if config.opt_bool(config.attack_modes, "WPS Pixie", True):
        attacks.append("wps")
    if config.opt_bool(config.attack_modes, "WPA", True):
        attacks.append("wpa")
    if config.opt_bool(config.attack_modes, "PMKID", False):
        attacks.append("pmkid")
    pv = ProgressView("SAVE")
    pv.push("Saved current config")
    pv.push("as: 'Custom %d'" % (len(config.presets)))
    pv.render()
    time.sleep(1.5)
    config.presets.append({
        "name": "Custom %d" % len(config.presets),
        "desc": "saved config",
        "attacks": attacks,
        "pixie": "wps" in attacks,
        "wps_time": config.opt_int(config.timing, "WPS Timeout", 180),
        "wpa_time": config.opt_int(config.timing, "WPA Timeout", 300),
        "deauth": config.opt_bool(config.attack_modes, "Deauth", True),
        "num_deauths": config.opt_int(config.timing, "Num Deauths", 5),
        "tool": config.opt_state(config.attack_modes, "WPS Tool", "reaver"),
        "band": config.opt_state(config.filters, "Band", "Both"),
        "scan": config.opt_int(config.timing, "Scan Time", 30),
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
    nets = _scan_with_progress(iface[0],
                               iface[1] if isinstance(iface, tuple) else "", "EXCLUDE")
    if not nets:
        ev = ProgressView("EXCLUDE")
        ev.push("ERR no networks")
        ev.render()
        time.sleep(2)
        return
    active = 0
    def _labels():
        out = []
        for n in nets:
            marker = "!" if n["bssid"] in config.Runtime.excluded_bssids else " "
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
        active = _move(active, len(nets), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "EXCLUDE")
            render_list(d, _labels(), active)
            display.show()
        elif ev["type"] == "press":
            bssid = nets[active]["bssid"]
            if bssid in config.Runtime.excluded_bssids:
                config.Runtime.excluded_bssids.remove(bssid)
            else:
                config.Runtime.excluded_bssids.append(bssid)
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
# Crack (manual — never automatic). Lists captured handshakes that have no PSK
# yet and offers on-box or on-server cracking.
# --------------------------------------------------------------------------
def crack_menu():
    entries = [e for e in load_cracked() if e.get("cap") and not e.get("psk")]
    if not entries:
        status("No captures to crack")
        time.sleep(1.5)
        return
    active = 0
    labels = [e.get("essid") or e.get("bssid") or "?" for e in entries]
    d = display.begin()
    box(d, "CRACK")
    render_list(d, labels, active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(entries), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "CRACK")
            render_list(d, labels, active)
            display.show()
        elif ev["type"] == "press":
            _crack_where(entries[active])
            return
        elif ev["type"] == "key1":
            return


def _crack_where(entry):
    labels = ["On-box (rockyou)", "On server (upload)"]
    active = 0
    d = display.begin()
    box(d, "CRACK")
    render_list(d, labels, active)
    display.show()
    while True:
        ev = wait_event()
        if not ev:
            continue
        active = _move(active, len(labels), ev)
        if ev["type"] in ("up", "down"):
            d = display.begin()
            box(d, "CRACK")
            render_list(d, labels, active)
            display.show()
        elif ev["type"] == "press":
            pv = ProgressView("CRACK")
            if active == 0:
                pv.push("running aircrack...")
                pv.render()
                res = crack_mod.crack_onbox(entry, progress_cb=pv.push)
                pv.push("KEY %s" % res["psk"] if res.get("ok")
                        else "ERR %s" % res.get("error"))
            else:
                res = crack_mod.crack_server(entry, progress_cb=pv.push)
                pv.push("OK sent to server" if res.get("ok")
                        else "ERR %s" % res.get("error"))
            pv.render()
            time.sleep(3)
            return
        elif ev["type"] == "key1":
            return


# --------------------------------------------------------------------------
# SysCheck
# --------------------------------------------------------------------------
def syscheck():
    pv = ProgressView("SYSCHECK")
    missing = [name for name, path in atk_tools.REQUIRED_TOOLS.items()
               if not atk_tools.tool_ok(path)]
    if missing:
        pv.push("tools MISSING: %s" % ", ".join(missing)[:18])
    else:
        pv.push("attack tools: ok")
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


def status(msg):
    d = display.begin()
    box(d, "INFO")
    theme.shadowed(d, msg[:config.MAX_CHARS_PER_LINE], (3, 40), font())
    display.show()
