# -*- coding: utf-8 -*-
"""wifi-box v2 entry point.

Initializes hardware (display, buttons, battery) with graceful fallbacks,
shows the splash, then runs the main menu. Never exits with a black screen:
top-level try/except ensures cleanup + a readable error.
"""
import sys
import os
import time
import threading
import traceback

# Make project root importable
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from hardware.display import display  # noqa: E402
from hardware.buttons import ButtonManager  # noqa: E402
import ui.menus as menus  # noqa: E402
from ui.screens import render_splash  # noqa: E402
from cracked_store import load_settings  # noqa: E402


def _restore_settings():
    """Restore persistent settings from ~/.local/share/wifi-box/settings.json."""
    s = load_settings()
    if not s:
        return
    from ui import theme
    if "theme" in s:
        theme.set_theme(s["theme"])
    if "exclusions" in s and isinstance(s["exclusions"], list):
        config.Runtime.excluded_essids = s["exclusions"]
    if "exclusions_bssid" in s and isinstance(s["exclusions_bssid"], list):
        config.Runtime.excluded_bssids = s["exclusions_bssid"]


def main():
    _t0 = time.time()
    # Splash on the LCD (if available) even before buttons init
    render_splash()
    _log("splash rendered %.2fs after app start" % (time.time() - _t0))

    buttons = ButtonManager()
    menus.set_button_manager(buttons)
    buttons.start()

    # Give buttons a moment to settle
    time.sleep(0.5)

    # Restore persistent settings
    _restore_settings()
    # Always fold hardcoded never-attack BSSIDs into Runtime exclusions.
    config.apply_hardcoded_excludes()

    if buttons.available:
        menus.main_menu()
    else:
        # No GPIO: headless fallback - log and exit cleanly
        _log("No GPIO available; running headless.")
        _log("Interfaces: %s" % iface_names())
        while True:
            time.sleep(60)


def iface_names():
    from attack import interface as iface_mod
    return [n for n, _ in iface_mod.get_interfaces()]


def _log(msg):
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        _log("FATAL: %s\n%s" % (e, tb))
        # Try to show the error on the LCD
        try:
            from ui import theme
            from PIL import ImageFont
            d = display.begin()
            d.rectangle((0, 0, config.WIDTH, config.HEIGHT),
                        fill=theme.background())
            theme.shadowed(d, "ERROR", (3, 10), ImageFont.load_default(),
                           color=(255, 0, 0))
            err = str(e)[:config.MAX_CHARS_PER_LINE]
            theme.shadowed(d, err, (3, 30), ImageFont.load_default())
            display.show()
            time.sleep(8)
        except Exception:  # noqa: BLE001
            pass
        raise
