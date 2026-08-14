# -*- coding: utf-8 -*-
"""Color themes and lightweight drawing helpers used across the UI."""
from PIL import ImageFont

import config

_current_theme = config.COLOR_THEMES[0]

# Real TrueType fonts read far better than the 6px PIL bitmap on the LCD.
_FONT_FILES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_font_cache = {}


def font(size=11):
    """Cached TrueType font at the given px size (falls back to bitmap)."""
    if size in _font_cache:
        return _font_cache[size]
    loaded = None
    for path in _FONT_FILES:
        try:
            loaded = ImageFont.truetype(path, size)
            break
        except Exception:  # noqa: BLE001
            continue
    if loaded is None:
        loaded = ImageFont.load_default()
    _font_cache[size] = loaded
    return loaded


def set_theme(index):
    global _current_theme
    _current_theme = config.COLOR_THEMES[index % len(config.COLOR_THEMES)]


def theme_index():
    for i, t in enumerate(config.COLOR_THEMES):
        if t is _current_theme:
            return i
    return 0


def palette():
    """Return the active theme dict."""
    return _current_theme


def text_color():
    return _current_theme["text"]


def highlight_color():
    return _current_theme["highlight"]


def accent_color():
    return _current_theme["accent"]


def shadow_color():
    return _current_theme["shadow"]


def background():
    return _current_theme["background"]


def shadowed(draw, text, xy, font, color=None, shadow=None):
    """Draw text with a 1px offset shadow. Returns width of drawn text."""
    if color is None:
        color = text_color()
    if shadow is None:
        shadow = shadow_color()
    x, y = xy
    draw.text((x + 1, y + 1), text, font=font, fill=shadow)
    draw.text((x, y), text, font=font, fill=color)
    try:
        return draw.textlength(text, font=font)
    except Exception:  # noqa: BLE001
        return len(text) * 6


def progress_bar(draw, x, y, w, h, percent, color=None, bg=None):
    """Draw a horizontal progress bar. percent 0..100."""
    if color is None:
        color = accent_color()
    if bg is None:
        bg = shadow_color()
    draw.rectangle((x, y, x + w, y + h), fill=bg, outline=text_color())
    fill_w = int(w * max(0, min(100, percent)) / 100)
    if fill_w > 0:
        draw.rectangle((x, y, x + fill_w, y + h), fill=color)


def circle(draw, cx, cy, r, color):
    """Draw a filled circle centered at (cx, cy)."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)


def mix(a, b, t):
    """Blend colour a toward b by fraction t (0..1)."""
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rrect(draw, box, radius, fill=None, outline=None):
    """Rounded rectangle with graceful fallback on older Pillow."""
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline)
    except (AttributeError, TypeError):
        draw.rectangle(box, fill=fill, outline=outline)


def pill(draw, x, y, label, font, fg=None, bg=None):
    """Filled rounded chip sized to its font. Returns (right, bottom) edges."""
    if bg is None:
        bg = accent_color()
    if fg is None:
        fg = background()
    try:
        bb = draw.textbbox((0, 0), label, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
    except Exception:  # noqa: BLE001
        tw, th = len(label) * 7, 11
    x2, y2 = x + tw + 8, y + th + 5
    rrect(draw, (x, y, x2, y2), 3, fill=bg)
    draw.text((x + 4, y + 1), label, font=font, fill=fg)
    return x2, y2


def gauge_color(remaining, warn=45, crit=15):
    """Green → orange → red as a countdown runs low (seconds)."""
    if remaining is None:
        return accent_color()
    if remaining <= crit:
        return (255, 60, 60)
    if remaining <= warn:
        return (255, 170, 0)
    return accent_color()


SPINNER = "|/-\\"


def spinner(tick):
    return SPINNER[tick % len(SPINNER)]


# ---------------------------------------------------------------------------
# FlipHUD design system — type scale + compact-HUD primitives.
# Look: true-black ground, one accent, hierarchy via thin grey elevation rules
# (no boxes). Inspired by Flipper FlipCTL + pwnagotchi, tuned for 128x128.
# ---------------------------------------------------------------------------
HERO = 15     # current target / phase
BODY = 11     # menu items, results, log
MICRO = 9     # status strip, counters, HUD labels


def dim_color():
    """Muted grey for spent / failed / secondary text."""
    return mix(background(), text_color(), 0.45)


def rule_color():
    """Faint 1px divider — 'elevation', not a border."""
    return mix(background(), text_color(), 0.22)


def hline(draw, y, x0=0, x1=None):
    if x1 is None:
        x1 = config.WIDTH
    draw.line((x0, y, x1, y), fill=rule_color())


def list_row(draw, x, y, w, label, selected, fnt, value=None):
    """One menu row, Flipper style: selected = filled accent bar with a left
    caret and background-coloured text; unselected = plain text. `value` (e.g. a
    toggle state) is right-aligned."""
    h = 13
    if selected:
        rrect(draw, (x, y, x + w, y + h), 3, fill=accent_color())
        fg = background()
        draw.text((x + 3, y + 1), "▸", font=fnt, fill=fg)   # ▸
        draw.text((x + 12, y + 1), label, font=fnt, fill=fg)
    else:
        fg = text_color()
        draw.text((x + 12, y + 1), label, font=fnt, fill=fg)
    if value is not None:
        try:
            vw = draw.textlength(value, font=fnt)
        except Exception:  # noqa: BLE001
            vw = len(value) * 6
        draw.text((x + w - vw - 4, y + 1), value, font=fnt, fill=fg)
    return y + h + 1


def page_dots(draw, x, y, pages, active):
    """Tiny right-edge page indicator."""
    for i in range(pages):
        col = accent_color() if i == active else rule_color()
        draw.ellipse((x + i * 5, y, x + i * 5 + 2, y + 2), fill=col)


def signal_bars(draw, x, y, dbm, bars=4, bw=2, gap=1, maxh=8):
    """4-bar signal glyph from a dBm value (-30 strong … -90 weak)."""
    if dbm is None:
        lit = 0
    else:
        lit = max(0, min(bars, int(round((dbm + 90) / 15.0))))   # -90→0 .. -30→4
    for i in range(bars):
        bh = int(maxh * (i + 1) / bars)
        bx = x + i * (bw + gap)
        col = accent_color() if i < lit else rule_color()
        draw.rectangle((bx, y + (maxh - bh), bx + bw, y + maxh), fill=col)
    return x + bars * (bw + gap)


def counters(draw, x, y, cracked, handshakes, fnt):
    """◆N cracked (accent) · ~N handshakes (highlight), compact."""
    s1 = "◆%d" % cracked          # ◆
    draw.text((x, y), s1, font=fnt, fill=accent_color())
    try:
        w1 = draw.textlength(s1 + " ", font=fnt)
    except Exception:  # noqa: BLE001
        w1 = len(s1) * 7
    draw.text((x + w1, y), "~%d" % handshakes, font=fnt, fill=highlight_color())
