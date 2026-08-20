# -*- coding: utf-8 -*-
"""Non-menu screens: splash, status bar, attack status, cracked viewer,
connect/upload progress. These are rendering + view-state helpers.
"""
import re
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
    """Bottom strip: just the galloping cat (battery % lives in the top-right
    header, so the bottom bar is dropped and the cat roams the full width)."""
    h, W = config.HEIGHT, config.WIDTH

    t = time.time()
    period = W                              # wrap over exactly one screen width
    base = (t * 26) % period                # ~26 px/s to the right
    phase = (t * 2.6) % 1.0                 # gait cycles ~2.6 strides/sec
    # Draw at base AND base-W: as the cat exits the right edge, the same cat
    # re-enters from the left in the same frame — a seamless loop with no gap.
    for cx in (base, base - period):
        cat.draw(draw, int(cx), h - cat.H, phase, color=(235, 235, 235))


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

    LIST_TOP = 27    # first scan-list row
    ROW_H = 12       # scan-list row height

    def __init__(self, title="ATTACK"):
        self.title = title
        self.target_cur = 0
        self.target_total = 0
        self.cur_essid = ""
        self.cur_bssid = ""       # currently-attacking BSSID (normalized)
        self.cur_phase = ""       # short code straight from the engine: HS/PIXIE/PMKID/DEAUTH/CRACK
        self.cur_countdown = None # int seconds remaining (as of the last event)
        self._cd_at = 0.0         # wall-clock when cur_countdown was set (live tick)
        self.scan_targets = 0     # live count while the engine is still scanning
        self.nets = []            # [{key, bssid, essid, signal, status, cred}]
        self.scroll = 0           # top visible scan-list row
        self._atk_key = None      # key currently marked "atk"
        self.started = False      # True once the engine emits its first real event
        self.last_msg = ""        # latest boot/message line, shown while starting
        self.cur_signal = None    # current target power (dBm)
        self.cur_clients = None   # associated clients on current target (None = n/a)
        self.target_start = time.time()   # when the current target began
        self.last_deauth = 0.0    # last time a deauth event arrived (for a pulse)
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

    @staticmethod
    def _norm_b(bssid):
        return config._norm_bssid(bssid) if bssid else ""

    @classmethod
    def _row_key(cls, bssid, essid):
        """Stable list key: always prefer normalized BSSID so status can update."""
        return cls._norm_b(bssid) or (essid or "")

    def _current_label(self):
        """ESSID for the header — never prefer raw MAC when we know the name."""
        essid = (self.cur_essid or "").strip()
        bssid = self._norm_b(self.cur_bssid)
        # Engine sometimes sets essid=bssid when the name is missing.
        if essid and essid.upper() != bssid and not self._looks_like_mac(essid):
            return essid
        for row in self.nets:
            if self._norm_b(row.get("bssid")) == bssid and row.get("essid"):
                name = row["essid"]
                if name and not self._looks_like_mac(name):
                    return name
        return essid or bssid or "..."

    @staticmethod
    def _looks_like_mac(s):
        s = (s or "").strip()
        if not s:
            return False
        # AA:BB:CC:DD:EE:FF or AABBCCDDEEFF
        return bool(re.match(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$", s) or
                    re.match(r"^[0-9A-Fa-f]{12}$", s))

    # -- event intake -----------------------------------------------------
    def handle_event(self, ev):
        t = ev.get("type", "")
        essid = ev.get("essid", "") or ""
        bssid = self._norm_b(ev.get("bssid", "") or "")
        if t == "message":
            # While the engine boots (monitor mode, killing procs) show its output.
            if not self.started and ev.get("text"):
                self.last_msg = ev["text"][:21]
            return
        self.started = True       # first real event -> the engine is up and scanning
        if t == "scan":
            # Live-growing scan list: merge each discovery into the row list so
            # the rows appear as they're found, not all at once at the end.
            self.scan_targets = ev.get("targets", 0)
            for item in (ev.get("found", []) or []):
                self._add_scan(item)
            self.cur_essid = ""
            self.cur_bssid = ""
            self.cur_phase = ""
            self.cur_countdown = None
        elif t == "attack":
            self.cur_bssid = bssid
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
            # Ensure the current target exists as a row keyed by BSSID.
            self._add_scan({"essid": essid, "bssid": bssid,
                            "signal": ev.get("signal")})
        elif t == "phase":
            if essid:
                self.cur_essid = essid
            if bssid:
                self.cur_bssid = bssid
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
            self._set(bssid, essid, "handshake")
        elif t == "pmkid":
            self._set(bssid, essid, "handshake", "pmkid")
        elif t == "cracked":
            # PSK = the connectable Wi-Fi password; a bare PIN is shown as "PIN …"
            # so it's clear it isn't the password yet.
            psk = ev.get("psk")
            pin = ev.get("pin")
            cred = psk if psk else ("PIN %s" % pin if pin else "?")
            self._set(bssid, essid, "cracked", str(cred))
        elif t == "failed":
            self._set(bssid, essid, "failed", ev.get("detail"))
        elif t == "skipped":
            self._set(bssid, essid, "skipped", ev.get("detail"))

    def _add_scan(self, item):
        """Add/merge a discovered network into the scrollable scan list."""
        essid = item.get("essid") or ""
        bssid = self._norm_b(item.get("bssid") or "")
        key = self._row_key(bssid, essid)
        if not key:
            return
        for row in self.nets:
            if row["key"] == key or (
                    bssid and self._norm_b(row.get("bssid")) == bssid):
                row["key"] = key if bssid else row["key"]
                # Never replace a real ESSID with a MAC-looking fallback.
                if essid and not self._looks_like_mac(essid):
                    row["essid"] = essid
                elif not row.get("essid"):
                    row["essid"] = essid
                row["bssid"] = bssid or row["bssid"]
                if item.get("signal") is not None:
                    row["signal"] = item["signal"]
                return
        self.nets.append({
            "key": key,
            "essid": essid if not self._looks_like_mac(essid) else "",
            "bssid": bssid,
            "signal": item.get("signal"), "status": ""})

    def _set(self, bssid, essid, status, cred=None):
        """Apply a result status to the scanned row (matched by normalized BSSID)."""
        bssid = self._norm_b(bssid)
        key = self._row_key(bssid, essid)
        if not key:
            return
        for row in self.nets:
            match = (row["key"] == key or
                     (bssid and self._norm_b(row.get("bssid")) == bssid) or
                     (essid and row.get("essid") == essid and not bssid))
            if not match:
                continue
            # Never let a later "failed" overwrite a real capture.
            rank = {"failed": 0, "skipped": 0, "handshake": 1, "cracked": 2}
            if rank.get(status, 0) >= rank.get(row.get("status"), 0):
                row["status"] = status
            if cred:
                row["cred"] = cred
            # Keep key stable on BSSID so later events keep hitting this row.
            if bssid:
                row["key"] = bssid
                row["bssid"] = bssid
            if essid and not self._looks_like_mac(essid):
                row["essid"] = essid
            return
        self.nets.append({"key": key,
                          "essid": essid if not self._looks_like_mac(essid) else "",
                          "bssid": bssid or key,
                          "signal": None, "status": status, "cred": cred})

    def summary(self):
        c = sum(1 for r in self.nets if r.get("status") == "cracked")
        h = sum(1 for r in self.nets if r.get("status") == "handshake")
        f = sum(1 for r in self.nets if r.get("status") in ("failed", "skipped"))
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

    # -- scan list + scrolling --------------------------------------------
    def seed_scan(self, nets):
        """Preload the list from a scan already done (Scan & Attack)."""
        for n in (nets or []):
            self._add_scan(n)

    def scroll_up(self):
        self.scroll = max(0, self.scroll - 1)

    def scroll_down(self):
        self.scroll = min(max(0, len(self.nets) - self._visible_rows()),
                          self.scroll + 1)

    def _visible_rows(self):
        return max(1, (config.HEIGHT - 15 - self.LIST_TOP) // self.ROW_H)

    def _ordered_nets(self):
        """Sort by attack order = strongest signal first (same as the engine's
        sort_by_signal)."""
        return sorted(self.nets, key=lambda r: r.get("signal") or 0, reverse=True)

    def _reveal_current(self):
        """Scroll so the row being attacked is on-screen."""
        key = self._row_key(self.cur_bssid, self.cur_essid)
        if not key:
            return
        ordered = self._ordered_nets()
        for i, row in enumerate(ordered):
            if row["key"] == key or (
                    self.cur_bssid and
                    self._norm_b(row.get("bssid")) == self._norm_b(self.cur_bssid)):
                vis = self._visible_rows()
                if i < self.scroll:
                    self.scroll = i
                elif i >= self.scroll + vis:
                    self.scroll = i - vis + 1
                return

    def _row_status(self, row):
        """(text, color) for the fixed right-hand status of a row."""
        st = row.get("status", "")
        if st == "cracked":
            return "KEY", theme.accent_color()
        if st == "handshake":
            return "HS", theme.highlight_color()
        if st == "failed":
            # Show soft-timeout reason when short enough for the LCD.
            detail = (row.get("cred") or "").strip()
            short = {
                "no beacon": "soft",
                "no assoc": "soft",
                "no pixie": "soft",
                "pin bf": "pinbf",
                "wps off": "off",
                "timeout": "t/o",
                "no pin": "nopin",
                "wps lock": "lock",
                "no psk": "nopsk",
            }.get(detail.lower(), "x")
            return short[:5], (255, 70, 70)
        if st == "skipped":
            return "-", theme.dim_color()
        return "", theme.dim_color()

    def render(self, force=False):
        """Attack screen: state + BSSID header, then a scrollable live list of
        scanned networks — each name marquees if it overflows, with a fixed
        status on the right. Battery% sits top-right."""
        now = time.time()
        if not force and now - self._last_paint < 0.1:   # cap full-frame pushes ~10fps
            return
        self._last_paint = now
        self._tick += 1
        d = display.begin()
        W, H = config.WIDTH, config.HEIGHT
        WHITE = theme.text_color()
        micro, body = font(theme.MICRO), font(theme.BODY)

        # --- top strip: state (left) · battery% (right) ---
        if not self.started:
            state = "STARTING"
        elif self.target_total == 0 and not self.cur_bssid and not self.cur_essid:
            state = "SCANNING"
        else:
            state = "ATTACKING"
        theme.shadowed(d, state, (3, 1), micro, color=WHITE)
        if not self.started:
            theme.shadowed(d, theme.spinner(self._tick), (58, 1), micro,
                           color=theme.accent_color())
        elif state == "ATTACKING" and self.cur_clients is not None:
            theme.shadowed(d, "C:%d" % self.cur_clients, (58, 1), micro,
                           color=theme.dim_color())
        pct, _ = battery.battery.read()
        if pct is not None:
            theme.shadowed(d, "%d%%" % pct, (W - 24, 1), micro,
                           color=theme.dim_color())
        theme.hline(d, 11)

        # --- second line: current target BSSID (marquee) + countdown ---
        cd = self._cd_str() if self.started else ""
        if not self.started:
            line2 = self.last_msg or ""
        elif state == "SCANNING":
            line2 = "scanning... %d" % self.scan_targets
        else:
            line2 = self._current_label()
        cdw = 0
        if cd:
            try:
                cdw = int(d.textlength(cd, font=micro)) + 8
            except Exception:  # noqa: BLE001
                cdw = 30
            theme.shadowed(d, cd, (W - cdw + 4, 13), micro, color=WHITE)
        lw = W - 6 - cdw
        if state == "SCANNING" or not self.started:
            theme.shadowed(d, self._fit(d, line2, body, lw), (3, 13), body,
                           color=theme.dim_color() if state == "SCANNING"
                           else theme.accent_color())
        else:
            theme.marquee(d, 3, 13, line2, lw, body, WHITE)
        theme.hline(d, 25)

        # --- scrollable scan list ---
        vis = self._visible_rows()
        ordered = self._ordered_nets()
        self.scroll = max(0, min(self.scroll, len(ordered) - vis))
        if ordered:
            n = len(ordered)
            if n > vis:
                track = (H - 15) - self.LIST_TOP
                thumb = max(8, track * vis // n)
                ty = self.LIST_TOP + (track - thumb) * self.scroll // max(1, n - vis)
                d.rectangle((W - 3, self.LIST_TOP, W - 2, H - 15),
                            fill=theme.rule_color())
                d.rectangle((W - 4, ty, W - 1, ty + thumb),
                            fill=theme.accent_color())
            y = self.LIST_TOP
            for row in ordered[self.scroll:self.scroll + vis]:
                self._render_row(d, y, row, body, micro, W)
                y += self.ROW_H
        else:
            theme.shadowed(d, "no networks yet", (3, 30), body,
                           color=theme.dim_color())

        status_bar(d)
        display.show()

    def _render_row(self, d, y, row, body, micro, W):
        """One scan-list row: name (marquee) left, fixed status right."""
        cur_key = self._row_key(self.cur_bssid, self.cur_essid)
        is_cur = bool(
            cur_key and (
                row["key"] == cur_key or
                (self.cur_bssid and
                 self._norm_b(row.get("bssid")) == self._norm_b(self.cur_bssid)) or
                (self.cur_essid and row.get("essid") == self.cur_essid and
                 not self._looks_like_mac(self.cur_essid))))
        name = row.get("essid") or row.get("bssid") or "?"
        if self._looks_like_mac(name) and row.get("bssid"):
            # Prefer showing ESSID; if missing, keep BSSID but it's a last resort.
            name = row.get("essid") or row.get("bssid")
        status, scol = self._row_status(row)
        if is_cur and not status:
            status, scol = self.cur_phase or theme.spinner(self._tick), \
                theme.highlight_color()
        if status:
            try:
                sw = int(d.textlength(status, font=micro)) + 6
            except Exception:  # noqa: BLE001
                sw = len(status) * 6 + 6
        else:
            sw = 0
        name_w = W - 6 - sw
        color = theme.text_color() if is_cur else theme.dim_color()
        try:
            fits = d.textlength(name, font=body) <= name_w
        except Exception:  # noqa: BLE001
            fits = len(name) * 6 <= name_w
        if fits:
            theme.shadowed(d, name, (3, y), body, color=color)
        else:
            theme.marquee(d, 3, y, name, name_w, body, color)
        if status:
            theme.shadowed(d, status, (W - sw + 3, y), micro, color=scol)


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
