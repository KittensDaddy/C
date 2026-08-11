# -*- coding: utf-8 -*-
"""Color themes and lightweight drawing helpers used across the UI."""
import config

_current_theme = config.COLOR_THEMES[0]


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
