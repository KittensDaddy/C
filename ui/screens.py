# -*- coding: utf-8 -*-
"""Non-menu screens: splash, status bar, attack status, cracked viewer,
connect/upload progress. These are rendering + view-state helpers.
"""
import time
import re
from PIL import ImageFont

import config
from ui import theme
from ui import cat
from hardware import battery
from hardware.display import display
from network import tailscale


def font():
    return ImageFont.load_default()


def _battery_bars(draw, x, y, pct):
    """5-segment battery icon. Lit bars = charge (~20%/bar); colour by level:
    low red, mid orange, high green."""
    w, ht, gap, segs = 36, 10, 1, 5
    outline = theme.mix(theme.background(), (255, 255, 255), 0.6)
    draw.rectangle((x, y, x + w, y + ht), outline=outline)
    draw.rectangle((x + w + 1, y + 3, x + w + 3, y + ht - 3), fill=outline)  # nub
    if pct is None:
        return
    n = max(1, min(segs, int(round(pct / 20.0))))
    if pct >= config.BATTERY_HIGH:
        col = (0, 220, 70)                # green
    elif pct >= config.BATTERY_MID:
        col = (255, 150, 0)               # orange
    else:
        col = (255, 45, 45)               # red
    seg = (w - 2 - gap * (segs - 1)) / float(segs)
    for i in range(n):
        sx = x + 2 + i * (seg + gap)
        draw.rectangle((round(sx), y + 2, round(sx + seg), y + ht - 2), fill=col)


def status_bar(draw):
    """Bottom bar: chunky 3-bar colour battery at left, a cat galloping across
    the bottom-right."""
    h, W = config.HEIGHT, config.WIDTH
    pct, _ = battery.battery.read()         # single read per frame
    _battery_bars(draw, 2, h - 11, pct)

    # Running cat loops across the bottom-right half of the screen.
    x0, x1 = 60, W + cat.W
    t = time.time()
    catx = x0 + int((t * 26) % (x1 - x0))   # ~26 px/s to the right, wraps
    phase = (t * 2.6) % 1.0                 # gait cycles ~2.6 strides/sec
    cat.draw(draw, catx, h - cat.H, phase, color=(235, 235, 235))


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
        self.scan_targets = 0     # live count while wifite is still scanning
        self.scan_clients = 0
        self.started = False      # True once wifite emits its first real event
        self.last_msg = ""        # latest boot/message line, shown while starting
        self.cur_signal = None    # current target power (dBm)
        self.cur_clients = 0      # associated clients on current target
        self.target_start = time.time()   # when the current target began
        self.last_deauth = 0.0    # last time a deauth event arrived (for a pulse)
        self.results = []         # list of [essid, status, cred/reason]
        self.start_time = time.time()
        self._tick = 0            # spinner animation counter
        self._phase_key = None    # (essid, phase) to reset the countdown gauge
        self._cd_max = None       # largest countdown seen this phase (for bar %)
        self.iface = ""           # attack interface name
        self.driver = ""          # its kernel driver (e.g. rtl8xxxu)
        self._last_paint = 0.0    # frame-rate cap (SPI paints are the bottleneck)

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
        if t == "message":
            # While wifite boots (monitor mode, killing procs) show its output.
            if not self.started and ev.get("text"):
                self.last_msg = ev["text"][:21]
            return
        self.started = True       # first real event -> wifite is up and scanning
        if t == "scan":
            self.scan_targets = ev.get("targets", 0)
            self.scan_clients = ev.get("clients", 0)
            self.cur_essid = ""
            self.cur_phase = ""
            self.cur_timeout = ""
        elif t == "attack":
            if essid != self.cur_essid:      # moved to a new target -> reset
                self.target_start = time.time()
                self.cur_signal = None
                self.cur_clients = 0
            self.cur_essid = essid
            self.cur_phase = ""       # no chip until a real phase arrives
            self.cur_timeout = ""
            if ev.get("current") and ev.get("total"):
                self.target_cur = ev["current"]
                self.target_total = ev["total"]
        elif t == "phase":
            if essid:
                self.cur_essid = essid
            self.cur_phase = _phase_label(ev.get("phase", ""), ev.get("detail", ""))
            self.cur_timeout = _timeout(ev.get("detail", ""))
            if ev.get("signal") is not None:
                self.cur_signal = ev["signal"]
            if ev.get("clients") is not None:
                self.cur_clients = ev["clients"]
            self._track_countdown()
        elif t == "deauth":
            self.last_deauth = time.time()
            if ev.get("signal") is not None:
                self.cur_signal = ev["signal"]
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
            self._set(essid, "failed", ev.get("detail"))
        elif t == "skipped":
            self._set(essid, "skipped", ev.get("detail"))

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
    def render(self, force=False):
        """Monochrome cyberpunk layout: text is white-on-dark with a shadow;
        only the battery and the progress/countdown bars carry colour.

        Frame-rate capped: wifite emits many lines/sec and each would trigger a
        full SPI repaint, so intermediate frames are dropped. State is still
        updated by handle_event; the next allowed frame shows the latest."""
        now = time.time()
        if not force and now - self._last_paint < 0.1:
            return
        self._last_paint = now
        self._tick += 1
        d = display.begin()
        W, H = config.WIDTH, config.HEIGHT
        WHITE = (245, 245, 245)
        fnt = font()
        d.rectangle((0, 0, W, H), fill=theme.background())
        c, h, f = self.summary()

        # Header: interface + driver (+ TS state, shown mono via +/-).
        d.rectangle((0, 0, W, 12), fill=theme.shadow_color())
        theme.shadowed(d, self._header_title(), (3, 1), fnt, color=WHITE)
        ts = tailscale.status()
        theme.shadowed(d, "TS+" if ts == "up" else "TS-", (W - 20, 1), fnt,
                       color=WHITE)
        d.line((0, 12, W, 12), fill=WHITE)

        if not self.started:
            # wifite is still initialising — monitor mode, killing conflicting
            # processes, loading. Show its boot output; scanning hasn't begun.
            theme.shadowed(d, "STARTING %s" % theme.spinner(self._tick),
                           (3, 15), fnt, color=WHITE)
            theme.shadowed(d, "%ds" % int(time.time() - self.start_time),
                           (W - 28, 15), fnt, color=WHITE)
            if self.last_msg:
                theme.shadowed(d, self.last_msg[:21], (3, 29), fnt,
                               color=theme.accent_color())
            theme.shadowed(d, "waiting for scan", (3, 45), fnt, color=WHITE)
            status_bar(d)
            display.show()
            return

        attacking = self.target_total > 0 or bool(self.cur_essid) or bool(self.results)
        # Header line: "3/12" + bar while attacking, else "SCANNING".
        theme.shadowed(d, "%d/%d" % (self.target_cur, self.target_total)
                       if self.target_total else ("SCANNING" if not attacking else "prep"),
                       (3, 15), fnt, color=WHITE)
        if self.target_total:
            theme.progress_bar(d, 34, 16, W - 66, 5,
                               100 * self.target_cur // self.target_total)
        theme.shadowed(d, "OK%d" % (c + h), (W - 28, 15), fnt, color=WHITE)

        if not attacking:
            # Scanning: live count that climbs as wifite finds APs / clients.
            theme.shadowed(d, "%s %d APs  %d STA" % (theme.spinner(self._tick),
                           self.scan_targets, self.scan_clients),
                           (3, 27), fnt, color=theme.accent_color())
        else:
            # Line 2: current target + phase + countdown (coloured gauge bar).
            cur = (self.cur_essid or "...")[:12]
            if self.cur_phase:
                cur = "%s %s" % (cur[:9], self.cur_phase[:6])
            theme.shadowed(d, cur[:21], (3, 27), fnt, color=WHITE)
            secs = _to_secs(self.cur_timeout)
            if self.cur_timeout:
                theme.shadowed(d, self.cur_timeout, (W - 28, 27), fnt, color=WHITE)
            # Line 3: signal · clients · deauth pulse · elapsed-on-target.
            theme.shadowed(d, self._info_line(), (3, 37), fnt, color=WHITE)
            # Line 4: countdown gauge (or a plain divider when no countdown).
            if self.cur_timeout:
                pct = int(100 * secs / self._cd_max) if (secs and self._cd_max) else 100
                theme.progress_bar(d, 3, 47, W - 6, 4, pct, color=theme.gauge_color(secs))
            else:
                d.line((0, 48, W, 48), fill=WHITE)

        if not attacking:
            d.line((0, 45, W, 45), fill=WHITE)

        # Running log of previous attacks, each quoted with its outcome/reason.
        y, bottom = 53, H - 20
        for essid, status, cred in self.results[-6:]:
            if y > bottom:
                break
            ok = status in ("cracked", "handshake")
            if status == "cracked" and cred:
                tail = cred[:9]
            elif ok:
                tail = "success"
            elif status == "skipped":
                tail = (cred or "skip")[:9]
            else:                                   # failed -> reason
                tail = (cred or "failed")[:9]
            glyph = "+" if ok else "-"
            theme.shadowed(d, ("%s %s %s" % (glyph, (essid or "?")[:9], tail))[:21],
                           (3, y), fnt, color=WHITE)
            y += 10

        status_bar(d)
        display.show()

    def _info_line(self):
        """signal · clients · deauth pulse · elapsed-on-target, kept compact."""
        parts = []
        if self.cur_signal is not None:
            parts.append("%ddB" % self.cur_signal)
        parts.append("%dc" % self.cur_clients)
        if time.time() - self.last_deauth < 2.5:      # recent deauth -> blink
            parts.append("DEA" if self._tick % 2 else "   ")
        parts.append("%ds" % int(time.time() - self.target_start))
        return " ".join(parts)[:21]


def _phase_label(phase, detail):
    """Short human phase tag for the current-attack line."""
    p = (phase or "").upper()
    if "PIXIE" in p:
        return "PIXIE"
    if "PIN" in p:
        return "PIN"
    if "HANDSHAKE" in p or ("WPA" in p and "DEAUTH" not in p):
        return "HS"           # client count is shown on the info line
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


def _format_cmd(argv, mode, iface, driver):
    """Break wifite argv into ordered display rows: (text, kind)."""
    args = list(argv or [])
    if args and args[0] == "sudo":
        args = args[1:]
    if args and "wifite" in args[0]:
        args = args[1:]
    rows = [("wifite", "bin"), ((mode or "custom").upper(), "mode")]
    if iface:
        rows.append(("%s %s" % (iface, driver or ""), "iface"))
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-i":                       # iface already shown above
            i += 2
            continue
        if a.startswith("-") and i + 1 < len(args) and not args[i + 1].startswith("-"):
            rows.append(("%s %s" % (a, args[i + 1]), "arg"))
            i += 2
        else:
            rows.append((a, "arg"))
            i += 1
    return rows


class CommandPreview:
    """Cyberpunk pre-flight page: types out the wifite command line by line so
    the user can recheck it before the attack fires. Monochrome text with hard
    shadows; only structural fills (header/scanlines) are non-text. Timing is
    driven by the caller (so a keypress can skip/cancel); this only renders."""

    def __init__(self, argv, mode="custom", iface="", driver=""):
        self.rows = _format_cmd(argv, mode, iface, driver)
        self._tick = 0

    def __len__(self):
        return len(self.rows)

    def render(self, upto):
        self._tick += 1
        d = display.begin()
        W, H = config.WIDTH, config.HEIGHT
        WHITE = (245, 245, 245)
        fnt = font()
        d.rectangle((0, 0, W, H), fill=(6, 6, 6))
        for yy in range(0, H, 3):           # scanline texture (dark grey)
            d.line((0, yy, W, yy), fill=(16, 16, 16))

        # inverted header band (white bar, black text) + blinking REC block
        d.rectangle((0, 0, W, 12), fill=WHITE)
        gx = 3 + (self._tick % 2)           # 1px jitter = glitch feel
        theme.shadowed(d, "RECHECK CMD", (gx, 1), fnt, color=(0, 0, 0),
                       shadow=(120, 120, 120))
        if self._tick % 2:
            d.rectangle((W - 10, 2, W - 4, 9), fill=(0, 0, 0))

        # revealed rows, scrolled so the newest stays visible
        visible = self.rows[:upto]
        lh, top = 10, 15
        max_rows = (H - top - 10) // lh
        start = max(0, len(visible) - max_rows)
        y = top
        prefix = {"bin": "", "mode": "> ", "iface": "# "}
        for text, kind in visible[start:]:
            theme.shadowed(d, (prefix.get(kind, "\xbb ") + text)[:21], (3, y),
                           fnt, color=WHITE)
            y += lh
        # blinking block cursor after the last revealed row
        if self._tick % 2 and y < H - 10:
            d.rectangle((4, y, 9, y + 7), fill=WHITE)

        theme.shadowed(d, "PRESS run  K1 back", (3, H - 9), fnt, color=WHITE)
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
