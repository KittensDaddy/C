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

    # -- rendering --------------------------------------------------------
    def render(self):
        d = display.begin()
        box(d, self.title)
        c, h, f = self.summary()

        # line 1: overall progress + trophy counts
        if self.target_total:
            head = "ATK %d/%d  +%d H:%d" % (self.target_cur, self.target_total, c, h)
        else:
            head = "ATK  +%d H:%d" % (c, h)
        theme.shadowed(d, head[:21], (3, 15), font(),
                       color=theme.highlight_color())

        # line 2: current target + phase + live countdown
        theme.shadowed(d, self._current_line()[:21], (3, 27), font(),
                       color=theme.accent_color())
        d.line((0, 38, config.WIDTH, 38), fill=theme.palette()["text"])

        # trophy list: cracked (persistent) then handshakes, failures collapsed
        cracked = [r for r in self.results if r[1] == "cracked"]
        shakes = [r for r in self.results if r[1] == "handshake"]
        y = 41
        bottom = config.HEIGHT - 11
        for essid, _, cred in cracked:
            if y > bottom:
                break
            text = "+%s %s" % ((essid or "?")[:9], (cred or "")[:9])
            theme.shadowed(d, text[:21], (3, y), font(), color=theme.accent_color())
            y += 10
        for essid, _, _ in shakes:
            if y > bottom - (10 if f else 0):
                break
            theme.shadowed(d, ("H %s" % (essid or "?"))[:21], (3, y), font(),
                           color=theme.palette()["text"])
            y += 10
        if f and y <= bottom:
            theme.shadowed(d, "- %d failed" % f, (3, y), font(),
                           color=theme.highlight_color())
        display.show()

    def _current_line(self):
        if not self.cur_essid and not self.cur_phase:
            return "> starting %ds" % int(time.time() - self.start_time)
        parts = [">"]
        if self.cur_essid:
            parts.append(self.cur_essid[:9])
        if self.cur_phase:
            parts.append(self.cur_phase)
        line = " ".join(parts)
        if self.cur_timeout:
            line += " " + self.cur_timeout
        return line


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
