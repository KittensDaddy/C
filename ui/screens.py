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
    """Compact live attack screen for tiny LCD."""

    def __init__(self, title="ATTACK"):
        self.title = title
        self.current = ""
        self.detail = ""
        self.results = []      # list of (essid, status, compact_str)
        self.percent = 2
        self.start_time = time.time()

    def handle_event(self, ev):
        t = ev.get("type", "")
        essid = ev.get("essid", "")
        if t == "scan":
            self.current = "SCAN %d/%d" % (ev.get("targets", 0),
                                           ev.get("clients", 0))
            self.percent = 3
        elif t == "attack":
            self.current = ">> %s" % (essid or "?")[:12]
            self._set(essid, "phase")
            self.percent = 10
        elif t == "handshake":
            self._set(essid, "handshake")
            self.detail = "HS %s" % (essid or "?")[:12]
            self.percent = 50
        elif t == "pmkid":
            self._set(essid, "handshake")
            self.detail = "PMKID %s" % (essid or "?")[:12]
            self.percent = 50
        elif t == "cracked":
            psk = ev.get("psk", "?")
            self._set(essid, "cracked", "%s=%s" % (essid or "?", str(psk)[:10]))
            self.detail = "KEY %s" % str(psk)[:12]
            self.percent = 100
        elif t == "failed":
            self._set(essid, "failed")
            self.detail = "FAIL %s" % (essid or "?")[:12]
            self.percent = 20
        elif t == "skipped":
            self._set(essid, "skipped")
            self.detail = "SKIP %s" % (essid or "?")[:12]
        elif t == "phase":
            phase = ev.get("phase", "")
            detail = ev.get("detail", "")
            if essid:
                self.current = "%s %s" % (essid or "?", _compact_phase(phase, detail))[:20]
                self._set(essid, "phase")
            self.percent = max(self.percent, 5)
        elif t == "message":
            text = ev.get("text", "")
            if text and len(text) < 40 and not text.startswith("elapsed"):
                self.detail = text[:21]
        elif t == "cracking":
            self._set(essid, "cracking")
            self.detail = "CRACK %s" % (essid or "?")[:12]
            self.percent = max(self.percent, 60)

    def _set(self, essid, status, compact_str=None):
        if not essid:
            return
        for row in self.results:
            if row[0] == essid:
                row[1] = status
                if compact_str:
                    row[2] = compact_str
                return
        self.results.append([essid, status, compact_str])

    def summary(self):
        c = sum(1 for r in self.results if r[1] == "cracked")
        h = sum(1 for r in self.results if r[1] == "handshake")
        f = sum(1 for r in self.results if r[1] == "failed")
        return c, h, f

    def render(self):
        d = display.begin()
        box(d, self.title)
        elapsed = int(time.time() - self.start_time)
        # line 1: current target/phase + elapsed
        theme.shadowed(d, "%s %ds" % (self.current[:16], elapsed),
                       (3, 15), font(), color=theme.palette()["text"])
        # line 2: progress bar
        theme.progress_bar(d, 3, 28, config.WIDTH - 6, 4,
                           max(2, self.percent))
        # line 3: latest detail
        if self.detail:
            theme.shadowed(d, self.detail[:21], (3, 36), font(),
                           color=theme.accent_color())
        # line 4: counters
        c, h, f = self.summary()
        theme.shadowed(d, "G:%d H:%d F:%d" % (c, h, f), (3, 48), font())
        # remaining lines: recent results
        y = 60
        for essid, status, desc in self.results[-4:]:
            if y > config.HEIGHT - 12:
                break
            glyph = {"cracked": "+", "handshake": "H",
                     "failed": "-", "skipped": "~",
                     "phase": ">", "cracking": "C"}.get(status, ".")
            col = theme.accent_color() if status in ("cracked", "handshake") \
                else theme.highlight_color() if status == "failed" \
                else theme.palette()["text"]
            text = desc or ("%s %s" % (glyph, essid))
            theme.shadowed(d, text[:21], (3, y), font(), color=col)
            y += 10
        display.show()


def _compact_phase(phase, detail):
    phase_u = (phase or "").upper()
    detail = detail or ""
    if "PIXIE" in phase_u:
        m = re.search(r"(\d+):(\d+)", detail)
        if m:
            return "PIXIE %s:%s" % (m.group(1), m.group(2))
        return "PIXIE %s" % detail[:6]
    if "PIN" in phase_u:
        m = re.search(r"(\d+):(\d+)", detail)
        if m:
            return "PIN %s:%s" % (m.group(1), m.group(2))
        return "PIN %s" % detail[:6]
    if "HANDSHAKE" in phase_u:
        m = re.search(r"clients[:=](\d+)", detail, re.I)
        if m:
            return "HS C:%s" % m.group(1)
        return "HS"
    if "DEAUTH" in phase_u:
        m = re.search(r"(\d+)\s*(?:/|of)\s*(\d+)", detail)
        if m:
            return "DEAUTH %s/%s" % (m.group(1), m.group(2))
        return "DEAUTH"
    return phase_u[:12]


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
