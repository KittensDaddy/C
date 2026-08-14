# -*- coding: utf-8 -*-
"""Non-menu screens: splash, status bar, attack status, cracked viewer,
connect/upload progress. These are rendering + view-state helpers.
"""
import time
from PIL import ImageFont

import config
from ui import theme
from ui import cat
from hardware import battery
from hardware.display import display


def font(size=None):
    """Default UI font = FlipHUD BODY; pass a size for HERO/MICRO."""
    return theme.font(size if size is not None else theme.BODY)


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
    # Charging: overlay a lightning bolt so it's clear power is flowing in.
    if getattr(battery.battery, "charging", False):
        cx, cy = x + w // 2, y + ht // 2
        draw.polygon([(cx, cy - 4), (cx + 3, cy - 4), (cx, cy),
                      (cx + 2, cy), (cx - 2, cy + 5), (cx, cy + 1),
                      (cx - 3, cy + 1)], fill=(255, 235, 0))


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
    """FlipHUD top strip: title (left) · battery% (right) · thin rule."""
    W = config.WIDTH
    tf = font(theme.BODY)
    while title and draw.textlength(title, font=tf) > W - 26:
        title = title[:-1]
    theme.shadowed(draw, title, (3, 0), tf, color=theme.highlight_color())
    pct, _ = battery.battery.read()
    if pct is not None:
        theme.shadowed(draw, "%d%%" % pct, (W - 22, 2), font(theme.MICRO),
                       color=theme.dim_color())
    theme.hline(draw, 13)


def box(draw, title):
    """Clear frame + header + status bar."""
    draw.rectangle((0, 0, config.WIDTH, config.HEIGHT),
                   fill=theme.background())
    header(draw, title)
    status_bar(draw)


def render_splash():
    d = display.begin()
    W, H = config.WIDTH, config.HEIGHT
    d.rectangle((0, 0, W, H), fill=theme.background())
    hero = font(theme.HERO)
    label = "WIFI-BOX"
    try:
        tw = d.textlength(label, font=hero)
    except Exception:  # noqa: BLE001
        tw = 80
    x = int((W - tw) / 2)
    theme.shadowed(d, label, (x, 44), hero, color=theme.accent_color())
    theme.hline(d, 64, x, x + int(tw))
    tag = "WPA · WPS · 2.4/5G"
    try:
        tgw = d.textlength(tag, font=font(theme.MICRO))
    except Exception:  # noqa: BLE001
        tgw = 100
    theme.shadowed(d, tag, (int((W - tgw) / 2), 70), font(theme.MICRO),
                   color=theme.dim_color())
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
        self.cur_phase = ""       # short code straight from the engine: HS/PIXIE/PMKID/DEAUTH/CRACK
        self.cur_countdown = None # int seconds remaining (as of the last event)
        self._cd_at = 0.0         # wall-clock when cur_countdown was set (live tick)
        self.scan_targets = 0     # live count while the engine is still scanning
        self.scan_found = []      # [{essid,signal}] discovered this scan
        self.started = False      # True once the engine emits its first real event
        self.last_msg = ""        # latest boot/message line, shown while starting
        self.cur_signal = None    # current target power (dBm)
        self.cur_clients = None   # associated clients on current target (None = n/a)
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
            # While the engine boots (monitor mode, killing procs) show its output.
            if not self.started and ev.get("text"):
                self.last_msg = ev["text"][:21]
            return
        self.started = True       # first real event -> the engine is up and scanning
        if t == "scan":
            self.scan_targets = ev.get("targets", 0)
            self.scan_found = ev.get("found", []) or []
            self.cur_essid = ""
            self.cur_phase = ""
            self.cur_countdown = None
        elif t == "attack":
            if essid != self.cur_essid:      # moved to a new target -> reset
                self.target_start = time.time()
                self.cur_signal = ev.get("signal")
                self.cur_clients = None
            self.cur_essid = essid
            self.cur_phase = ""       # no chip until a real phase arrives
            self.cur_countdown = None
            if ev.get("current") and ev.get("total"):
                self.target_cur = ev["current"]
                self.target_total = ev["total"]
        elif t == "phase":
            if essid:
                self.cur_essid = essid
            self.cur_phase = ev.get("phase", "") or ""   # already a short code
            self.cur_countdown = ev.get("countdown")
            self._cd_at = time.time()                    # anchor for the live tick
            if ev.get("signal") is not None:
                self.cur_signal = ev["signal"]
            if ev.get("clients") is not None:
                self.cur_clients = ev["clients"]
            self._track_countdown(ev.get("cd_max"))
        elif t == "deauth":
            self.last_deauth = time.time()
            if ev.get("signal") is not None:
                self.cur_signal = ev["signal"]
        elif t == "cracking":
            self.cur_phase = "CRACK"
            self.cur_countdown = None
        elif t == "handshake":
            self._set(essid, "handshake")
        elif t == "pmkid":
            self._set(essid, "handshake", "pmkid")
        elif t == "cracked":
            # PSK = the connectable Wi-Fi password; a bare PIN is shown as "PIN …"
            # so it's clear it isn't the password yet.
            psk = ev.get("psk")
            pin = ev.get("pin")
            cred = psk if psk else ("PIN %s" % pin if pin else "?")
            self._set(essid, "cracked", str(cred))
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

    def _track_countdown(self, cd_max=None):
        """Track the phase length so the gauge can deplete. Prefer the engine's
        cd_max; else remember the largest countdown seen this phase."""
        key = (self.cur_essid, self.cur_phase)
        if key != self._phase_key:
            self._phase_key = key
            self._cd_max = None
        if cd_max:
            self._cd_max = max(self._cd_max or 0, cd_max)
        elif self.cur_countdown is not None:
            self._cd_max = max(self._cd_max or 0, self.cur_countdown)

    # -- rendering --------------------------------------------------------
    @staticmethod
    def _fit(d, text, fnt, maxw):
        """Clip text to fit maxw px (proportional font — measure, don't count)."""
        if not text:
            return ""
        try:
            while text and d.textlength(text, font=fnt) > maxw:
                text = text[:-1]
        except Exception:  # noqa: BLE001
            return text[:max(1, int(maxw / 6))]
        return text

    def _live_cd(self):
        """Countdown decremented locally so it ticks every second between the
        engine's (sparser) heartbeat events."""
        if self.cur_countdown is None:
            return None
        return max(0, self.cur_countdown - int(time.time() - self._cd_at))

    def _cd_str(self):
        s = self._live_cd()
        return "" if s is None else "%d:%02d" % (s // 60, s % 60)

    def render(self, force=False):
        """FlipHUD live attack screen: true-black ground, one accent, thin grey
        elevation rules. Frame-rate capped — the engine emits many events/sec, so
        intermediate frames drop; state is kept by handle_event."""
        now = time.time()
        if not force and now - self._last_paint < 0.2:   # cap full-frame pushes ~5fps
            return
        self._last_paint = now
        self._tick += 1
        d = display.begin()
        W, H = config.WIDTH, config.HEIGHT
        WHITE = theme.text_color()
        micro, body, hero = font(theme.MICRO), font(theme.BODY), font(theme.HERO)
        c, h, f = self.summary()

        # --- top status strip: iface (left) · battery% + TS dot (right) ---
        theme.shadowed(d, self._fit(d, self._header_title(), micro, W - 42),
                       (3, 1), micro, color=WHITE)
        pct, _ = battery.battery.read()
        if pct is not None:
            theme.shadowed(d, "%d%%" % pct, (W - 24, 1), micro,
                           color=theme.dim_color())
        theme.hline(d, 11)

        if not self.started:
            theme.shadowed(d, "STARTING %s" % theme.spinner(self._tick),
                           (3, 22), body, color=WHITE)
            if self.last_msg:
                theme.shadowed(d, self._fit(d, self.last_msg, micro, W - 6),
                               (3, 42), micro, color=theme.accent_color())
            status_bar(d)
            display.show()
            return

        attacking = self.target_total > 0 or bool(self.cur_essid) or bool(self.results)

        # --- counter row: ATK c/t · tick bar · ◆cracked ~hs ---
        head = "ATK %d/%d" % (self.target_cur, self.target_total) if self.target_total \
            else ("SCAN" if not attacking else "PREP")
        theme.shadowed(d, head, (3, 13), micro, color=WHITE)
        if self.target_total:
            theme.progress_bar(d, 52, 15, 30, 4,
                               100 * self.target_cur // max(1, self.target_total))
        theme.counters(d, W - 40, 13, c, h, micro)

        if not attacking:
            # Scanning: live discovery list — signal bars + SSID, newest first.
            theme.shadowed(d, "%s scanning..." % theme.spinner(self._tick),
                           (3, 28), body, color=theme.accent_color())
            theme.hline(d, 43)
            y = 46
            for item in reversed(self.scan_found[-6:]):
                if y > H - 15:
                    break
                theme.signal_bars(d, 3, y + 1, item.get("signal"))
                theme.shadowed(d, self._fit(d, item.get("essid") or "?", body,
                               W - 22), (18, y - 1), body, color=WHITE)
                y += 12
        else:
            # --- hero: current target + phase pill ---
            name = self.cur_essid or "..."
            phase = self.cur_phase
            pill_w = 0
            if phase:
                try:
                    pill_w = int(d.textlength(phase, font=micro)) + 12
                except Exception:  # noqa: BLE001
                    pill_w = len(phase) * 7 + 12
                theme.pill(d, W - pill_w - 1, 25, phase, micro)
            theme.shadowed(d, self._fit(d, name, hero, W - pill_w - 6),
                           (3, 24), hero, color=WHITE)

            # --- HUD line: signal bars · clients · deauth pulse · countdown ---
            hx = theme.signal_bars(d, 3, 45, self.cur_signal) + 6
            if self.cur_clients is not None:      # only meaningful for WPA
                theme.shadowed(d, "%d sta" % self.cur_clients, (hx, 44), micro,
                               color=WHITE)
            if time.time() - self.last_deauth < 2.5 and self._tick % 2:
                theme.shadowed(d, "DEAUTH", (hx + 34, 44), micro,
                               color=theme.highlight_color())
            cd = self._cd_str()
            if cd:
                theme.shadowed(d, cd, (W - 26, 44), micro, color=WHITE)

            # --- countdown gauge (live) ---
            secs = self._live_cd()
            if secs is not None and self._cd_max:
                pct = int(100 * secs / self._cd_max)
                theme.progress_bar(d, 3, 56, W - 6, 4, pct,
                                   color=theme.gauge_color(secs))
            else:
                theme.hline(d, 58)

        # --- results log: newest first, colored glyphs ---
        y, bottom = 65, H - 15
        glyphs = {"cracked": ("◆", theme.accent_color()),
                  "handshake": ("~", theme.highlight_color()),
                  "failed": ("·", theme.dim_color()),
                  "skipped": ("·", theme.dim_color())}
        for essid, status, cred in reversed(self.results[-6:]):
            if y > bottom:
                break
            glyph, col = glyphs.get(status, ("·", theme.dim_color()))
            if status == "cracked" and cred:
                tail = str(cred)
            elif status == "handshake":
                tail = "got hs"
            else:
                tail = str(cred or status)
            d.text((3, y), glyph, font=body, fill=col)
            theme.shadowed(d, self._fit(d, essid or "?", body, 64),
                           (13, y), body, color=WHITE)
            tw = 44
            theme.shadowed(d, self._fit(d, tail, body, tw), (W - tw - 2, y),
                           body, color=theme.dim_color())
            y += 12

        status_bar(d)
        display.show()


def _format_plan(lines, mode, iface, driver):
    """Turn a native attack plan (list of 'key: value' strings) into display
    rows: (text, kind)."""
    rows = [((mode or "custom").upper(), "mode")]
    if iface:
        rows.append(("%s %s" % (iface, driver or ""), "iface"))
    for ln in (lines or []):
        rows.append((str(ln), "arg"))
    return rows


class CommandPreview:
    """Cyberpunk pre-flight page: types out the native attack plan line by line
    so the user can recheck it before the attack fires. Monochrome text with hard
    shadows; only structural fills (header/scanlines) are non-text. Timing is
    driven by the caller (so a keypress can skip/cancel); this only renders."""

    def __init__(self, lines, mode="custom", iface="", driver=""):
        self.rows = _format_plan(lines, mode, iface, driver)
        self._tick = 0

    def __len__(self):
        return len(self.rows)

    def render(self, upto):
        self._tick += 1
        d = display.begin()
        W, H = config.WIDTH, config.HEIGHT
        WHITE = theme.text_color()
        body, micro = font(theme.BODY), font(theme.MICRO)
        d.rectangle((0, 0, W, H), fill=theme.background())
        for yy in range(14, H - 14, 3):     # faint scanline texture
            d.line((0, yy, W, yy), fill=theme.mix(theme.background(),
                                                  theme.text_color(), 0.06))

        # accent header bar (bg-coloured text) + blinking REC dot
        d.rectangle((0, 0, W, 12), fill=theme.accent_color())
        d.text((3, 0), "ATTACK PLAN", font=body, fill=theme.background())
        if self._tick % 2:
            theme.circle(d, W - 7, 6, 3, theme.background())

        # revealed rows, scrolled so the newest stays visible
        visible = self.rows[:upto]
        lh, top = 12, 16
        max_rows = (H - top - 12) // lh
        start = max(0, len(visible) - max_rows)
        y = top
        prefix = {"bin": "", "mode": "▸ ", "iface": "# "}
        color = {"bin": theme.dim_color(), "mode": theme.highlight_color(),
                 "iface": theme.dim_color()}
        for text, kind in visible[start:]:
            txt = prefix.get(kind, "» ") + text
            while txt and d.textlength(txt, font=body) > W - 6:
                txt = txt[:-1]
            theme.shadowed(d, txt, (3, y), body, color=color.get(kind, WHITE))
            y += lh
        if self._tick % 2 and y < H - 12:   # blinking block cursor
            d.rectangle((4, y + 1, 8, y + 8), fill=theme.accent_color())

        theme.shadowed(d, "PRESS run   K1 back", (3, H - 11), micro,
                       color=theme.dim_color())
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
        body = font(theme.BODY)
        y = 17
        for ln in self.lines:
            if y > config.HEIGHT - 15:
                break
            low = ln.lower()
            if ln.startswith(("OK", "KEY")) or "done" in low:
                color = theme.accent_color()
            elif ln.startswith("HS"):
                color = theme.highlight_color()
            elif ln.startswith("ERR"):
                color = (255, 60, 60)
            else:
                color = theme.text_color()
            txt = ln
            while txt and d.textlength(txt, font=body) > config.WIDTH - 6:
                txt = txt[:-1]
            theme.shadowed(d, txt, (3, y), body, color=color)
            y += 13
        display.show()
