# -*- coding: utf-8 -*-
"""Non-menu screens: splash, status bar, attack status, cracked viewer,
connect/upload progress. These are rendering + view-state helpers.
"""
import time
import re
from PIL import ImageFont

import config
from ui import theme
from hardware import battery
from hardware.display import display
from network import tailscale


def font():
    return ImageFont.load_default()


def status_bar(draw):
    """Draw bottom battery bar + voltage label."""
    h = config.HEIGHT
    battery.battery.read()
    bw = config.WIDTH - 30
    battery.battery.bar(draw, 12, h - 8, bw, 3)
    # voltage label
    _, vstr = battery.battery.read()
    theme.shadowed(draw, vstr, (bw + 14, h - 9), font(),
                   color=(200, 200, 200))


def header(draw, title, show_ts=True):
    draw.rectangle((0, 0, config.WIDTH, 11), fill=theme.shadow_color())
    theme.shadowed(draw, title, (3, 1), font(), color=theme.highlight_color())
    if show_ts:
        st = tailscale.status()
        col = theme.accent_color() if st == "up" else (255, 0, 0)
        theme.shadowed(draw, "TS", (config.WIDTH - 18, 1), font(), color=col)
    draw.line((0, 12, config.WIDTH, 12), fill=theme.palette()["text"])


def box(draw, title):
    """Clear frame + header + status bar."""
    draw.rectangle((0, 0, config.WIDTH, config.HEIGHT),
                   fill=theme.background())
    header(draw, title)
    status_bar(draw)


def render_splash():
    d = display.begin()
    # simple centered logo
    label = "WIFI-BOX v2"
    d.rectangle((0, 0, config.WIDTH, config.HEIGHT), fill=theme.background())
    theme.shadowed(d, label, (config.WIDTH // 2 - 40, config.HEIGHT // 2 - 6),
                   font(), color=theme.accent_color())
    status_bar(d)
    display.show()


class AttackStatus:
    """Trophy-first live attack screen for tiny LCD.

    Layout (128x128, ~21 chars/line):
        overall : ATK 3/12  +2 H:4        target index, cracked/handshake counts
        current : > HomeNet WPS 0:38      essid + phase + live countdown
        trophies: cracked (persistent) -> handshakes -> collapsed "- N failed"
    """

    def __init__(self, title="ATTACK"):
        self.title = title
        self.target_cur = 0
        self.target_total = 0
        self.cur_essid = ""
        self.cur_phase = ""       # short label: SCAN / WPS / PIXIE / PIN / HS / DEAUTH / CRACK
        self.cur_timeout = ""     # "0:38" countdown pulled from wifite, if any
        self.results = []         # list of [essid, status, cred]
        self.start_time = time.time()
        self._tick = 0            # spinner animation counter
        self._phase_key = None    # (essid, phase) to reset the countdown gauge
        self._cd_max = None       # largest countdown seen this phase (for bar %)
        self.iface = ""           # attack interface name
        self.driver = ""          # its kernel driver (e.g. rtl8xxxu)

    def set_iface(self, name, driver=""):
        self.iface = name or ""
        self.driver = driver or ""

    def _header_title(self):
        """Header shows the live attack interface + driver, not a static label."""
        name = getattr(config.Runtime, "monitor_iface", None) or self.iface
        if not name:
            return self.title
        return ("%s %s" % (name, self.driver)).strip()[:16]

    # -- event intake -----------------------------------------------------
    def handle_event(self, ev):
        t = ev.get("type", "")
        essid = ev.get("essid", "") or ""
        if t == "scan":
            self.cur_essid = ""
            self.cur_phase = "SCAN %d/%d" % (ev.get("targets", 0),
                                             ev.get("clients", 0))
            self.cur_timeout = ""
        elif t == "attack":
            self.cur_essid = essid
            self.cur_phase = "start"
            self.cur_timeout = ""
            if ev.get("current") and ev.get("total"):
                self.target_cur = ev["current"]
                self.target_total = ev["total"]
        elif t == "phase":
            if essid:
                self.cur_essid = essid
            self.cur_phase = _phase_label(ev.get("phase", ""), ev.get("detail", ""))
            self.cur_timeout = _timeout(ev.get("detail", ""))
            self._track_countdown()
        elif t == "deauth":
            self.cur_phase = "DEAUTH %s/%s" % (ev.get("current", "?"),
                                               ev.get("total", "?"))
            self.cur_timeout = ""
        elif t == "cracking":
            self.cur_phase = "CRACK"
            self.cur_timeout = ""
        elif t == "handshake":
            self._set(essid, "handshake")
        elif t == "pmkid":
            self._set(essid, "handshake", "pmkid")
        elif t == "cracked":
            self._set(essid, "cracked", str(ev.get("psk", "?")))
        elif t == "failed":
            self._set(essid, "failed")
        elif t == "skipped":
            self._set(essid, "skipped")

    def _set(self, essid, status, cred=None):
        if not essid:
            return
        for row in self.results:
            if row[0] == essid:
                # Never let a later "failed" overwrite a real capture.
                rank = {"failed": 0, "skipped": 0, "handshake": 1, "cracked": 2}
                if rank.get(status, 0) >= rank.get(row[1], 0):
                    row[1] = status
                if cred:
                    row[2] = cred
                return
        self.results.append([essid, status, cred])

    def summary(self):
        c = sum(1 for r in self.results if r[1] == "cracked")
        h = sum(1 for r in self.results if r[1] == "handshake")
        f = sum(1 for r in self.results if r[1] in ("failed", "skipped"))
        return c, h, f

    def _track_countdown(self):
        """Remember the largest countdown per phase so the gauge can deplete."""
        key = (self.cur_essid, self.cur_phase)
        if key != self._phase_key:
            self._phase_key = key
            self._cd_max = None
        secs = _to_secs(self.cur_timeout)
        if secs is not None and (self._cd_max is None or secs > self._cd_max):
            self._cd_max = secs

    # -- rendering --------------------------------------------------------
    def render(self):
        self._tick += 1
        pal = theme.palette()
        d = display.begin()
        box(d, self._header_title())
        c, h, f = self.summary()
        W = config.WIDTH

        # -- Zone A: overall run progress (target N/M) + trophy counts -----
        theme.shadowed(d, "%d/%d" % (self.target_cur, self.target_total)
                       if self.target_total else "--",
                       (3, 15), font(), color=pal["text"])
        bx0, bx1 = 30, W - 34
        theme.progress_bar(d, bx0, 16, bx1 - bx0, 5,
                           (100 * self.target_cur // self.target_total)
                           if self.target_total else 0)
        theme.shadowed(d, "+%d" % c, (W - 30, 15), font(),
                       color=theme.accent_color())
        theme.shadowed(d, "H%d" % h, (W - 16, 15), font(), color=pal["text"])

        # -- Zone B: the "current attack" card ----------------------------
        card = (2, 25, W - 3, 50)
        theme.rrect(d, card, 3, fill=theme.mix(pal["background"], pal["text"], 0.10),
                    outline=theme.accent_color())
        if self.cur_essid or self.cur_phase:
            # essid + spinner
            theme.shadowed(d, (self.cur_essid or "scanning")[:16], (6, 27),
                           font(), color=theme.accent_color())
            d.text((W - 12, 27), theme.spinner(self._tick), font=font(),
                   fill=pal["text"])
            # phase pill + live countdown gauge
            secs = _to_secs(self.cur_timeout)
            if self.cur_phase:
                px = theme.pill(d, 6, 38, self.cur_phase[:12], font(),
                                bg=theme.mix(pal["background"], theme.accent_color(), 0.7))
            else:
                px = 6
            if self.cur_timeout:
                theme.shadowed(d, self.cur_timeout, (W - 32, 38), font(),
                               color=theme.gauge_color(secs))
                pct = int(100 * secs / self._cd_max) if (secs and self._cd_max) else 100
                theme.progress_bar(d, px + 4, 40, (W - 36) - (px + 4), 5, pct,
                                   color=theme.gauge_color(secs))
        else:
            theme.shadowed(d, "starting %ds" % int(time.time() - self.start_time),
                           (6, 33), font(), color=pal["text"])

        # -- Zone C: trophies (cracked persistent, then handshakes) -------
        cracked = [r for r in self.results if r[1] == "cracked"]
        shakes = [r for r in self.results if r[1] == "handshake"]
        y, bottom = 54, config.HEIGHT - 11
        for essid, _, cred in cracked:
            if y > bottom:
                break
            theme.circle(d, 6, y + 3, 2, theme.accent_color())
            theme.shadowed(d, ("%s %s" % ((essid or "?")[:9], cred or ""))[:19],
                           (12, y), font(), color=theme.accent_color())
            y += 11
        for essid, _, _ in shakes:
            if y > bottom - (11 if f else 0):
                break
            theme.circle(d, 6, y + 3, 2, pal["text"])
            theme.shadowed(d, (essid or "?")[:19], (12, y), font(), color=pal["text"])
            y += 11
        if f and y <= bottom:
            theme.circle(d, 6, y + 3, 2, (255, 60, 60))
            theme.shadowed(d, "%d failed" % f, (12, y), font(), color=(255, 60, 60))
        display.show()


def _phase_label(phase, detail):
    """Short human phase tag for the current-attack line."""
    p = (phase or "").upper()
    if "PIXIE" in p:
        return "PIXIE"
    if "PIN" in p:
        return "PIN"
    if "HANDSHAKE" in p or ("WPA" in p and "DEAUTH" not in p):
        m = re.search(r"clients[:=](\d+)", detail or "", re.I)
        return "HS c%s" % m.group(1) if m else "HS"
    if "DEAUTH" in p:
        return "DEAUTH"
    if "CRACK" in p:
        return "CRACK"
    return (p.replace("WPS ", "").replace("WPA ", "") or "atk")[:6]


def _timeout(detail):
    """Pull a live countdown (m:ss) out of wifite's phase detail, if present."""
    detail = detail or ""
    m = re.search(r"timeout[:=]\s*(\d+):(\d+)", detail, re.I)
    if not m:
        m = re.search(r"\[(\d+):(\d+)\]", detail)      # WPS "[00:38] Sending M5"
    if not m:
        m = re.search(r"\b(\d+):(\d{2})\b", detail)
    if not m:
        return ""
    return "%d:%02d" % (int(m.group(1)), int(m.group(2)))


def _to_secs(mmss):
    """'3:42' -> 222 seconds, or None."""
    m = re.match(r"(\d+):(\d{2})$", mmss or "")
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


class ProgressView:
    """Simple full-screen status text view (connect/upload)."""

    def __init__(self, title="STATUS"):
        self.title = title
        self.lines = []

    def push(self, msg):
        self.lines.append(msg)
        if len(self.lines) > 7:
            self.lines = self.lines[-7:]

    def render(self):
        d = display.begin()
        box(d, self.title)
        y = 16
        for ln in self.lines:
            if y > config.HEIGHT - 12:
                break
            color = theme.accent_color() if ln.startswith("OK") or \
                "done" in ln.lower() else theme.palette()["text"]
            if ln.startswith("ERR"):
                color = (255, 0, 0)
            theme.shadowed(d, ln[:config.MAX_CHARS_PER_LINE], (3, y), font(),
                           color=color)
            y += 10
        display.show()
