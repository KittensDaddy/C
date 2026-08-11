# -*- coding: utf-8 -*-
"""Non-menu screens: splash, status bar, attack status, cracked viewer,
connect/upload progress. These are rendering + view-state helpers.
"""
import time
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
    """View state for the live attack screen."""

    def __init__(self, title="ATTACK"):
        self.title = title
        self.phase = "starting"
        self.label = ""
        self.results = []      # list of (essid, status, psk)
        self.phase_percent = None
        self.start_time = time.time()

    def handle_event(self, ev):
        t = ev.get("type")
        essid = ev.get("essid")
        if t == "attack":
            self.phase = "attacking %s" % (essid or "?")
            self.label = ""
            if essid:
                self.results.append([essid, "scan", None])
        elif t == "handshake":
            self._set(essid, "handshake")
            self.label = "handshake!"
        elif t == "cracked":
            self._set(essid, "cracked", ev.get("psk"))
            self.label = "cracked!"
        elif t == "failed":
            self._set(essid, "failed")
            self.label = "failed"
        elif t == "phase":
            self.phase = "%s: %s" % (essid or "?", ev.get("phase", ""))
            self._set(essid, "phase")
        elif t == "message":
            self.label = ev.get("text", "")

    def _set(self, essid, status, psk=None):
        if not essid:
            return
        for row in self.results:
            if row[0] == essid:
                row[1] = status
                row[2] = psk
                return
        self.results.append([essid, status, psk])

    def summary(self):
        c = sum(1 for r in self.results if r[1] == "cracked")
        h = sum(1 for r in self.results if r[1] == "handshake")
        f = sum(1 for r in self.results if r[1] == "failed")
        return c, h, f

    def render(self):
        d = display.begin()
        box(d, self.title)
        # header line: target
        elapsed = int(time.time() - self.start_time)
        theme.shadowed(d, "%s  %ds" % (self.phase, elapsed), (3, 15),
                       font(), color=theme.palette()["text"])
        # progress bar
        theme.progress_bar(d, 3, 28, config.WIDTH - 6, 4, 30)
        # label
        if self.label:
            theme.shadowed(d, self.label[:config.MAX_CHARS_PER_LINE],
                           (3, 36), font(), color=theme.accent_color())
        # counters
        c, h, f = self.summary()
        theme.shadowed(d, "G:%d H:%d F:%d" % (c, h, f), (3, 48), font())
        # results
        y = 60
        for essid, status, psk in self.results[-4:]:
            if y > config.HEIGHT - 12:
                break
            glyph = {"cracked": "OK", "handshake": "HS",
                     "failed": "XX"}.get(status, "..")
            col = theme.accent_color() if status in ("cracked", "handshake") \
                else theme.highlight_color() if status == "failed" \
                else theme.palette()["text"]
            theme.shadowed(d, "%s %s" % (glyph, essid), (3, y), font(),
                           color=col)
            y += 10
        display.show()


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
