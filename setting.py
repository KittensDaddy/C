from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from INA219 import INA219
from PIL import ImageFont, Image, ImageDraw
import LCD_1in44


OPTIONS_PER_PAGE = 11
DEBOUNCE_TIME = 0.15

KEY_UP_PIN = 6
KEY_DOWN_PIN = 19
KEY_LEFT_PIN = 5
KEY_RIGHT_PIN = 26
KEY_PRESS_PIN = 13
KEY1_PIN = 21
KEY2_PIN = 20
KEY3_PIN = 16

CRACKED_FILE = Path("/home/sun/cracked.json")


@dataclass(frozen=True)
class CommandProfile:
    name: str
    flags: tuple[str, ...]
    description: str = ""


@dataclass
class RuntimeState:
    stealth_mode_active: threading.Event = field(default_factory=threading.Event)
    wireless_interfaces: list[tuple[str, str]] = field(default_factory=list)
    selected_interface: tuple[str, str] | None = None
    essids: list[tuple[str, str]] = field(default_factory=list)
    excluded_essids: list[str] = field(default_factory=list)
    selected_essid: str | None = None
    selected_bssid: str | None = None
    attack_profile_key: str = "auto"
    scan_time: int = 10
    wps_time: int | None = None
    deauth_timeout: int | None = None
    all_band: bool = False
    clients_only: bool = False
    no_deauths: bool = False

    def selected_interface_name(self) -> str | None:
        if self.selected_interface is None:
            return None
        return self.selected_interface[0]


app_state = RuntimeState()
stealth_mode_active = app_state.stealth_mode_active
state = {"stealth_mode_active": stealth_mode_active}

ATTACK_PROFILES = {
    "auto": CommandProfile("Auto", tuple(), "Let wifite choose the attack path."),
    "pixie": CommandProfile("Pixie WPS", ("--wps-only", "--pixie"), "Focus on WPS Pixie Dust targets."),
    "pmkid": CommandProfile("PMKID", ("--pmkid", "--no-wps"), "PMKID-only collection."),
    "handshake": CommandProfile("Handshake", ("--no-pmkid", "--no-wps"), "Traditional WPA handshake capture."),
    "pixie_quick": CommandProfile("Pixie Quick 30", ("--wps-only", "--pixie", "--wps-time", "30"), "Fast 30 second WPS pass."),
    "handshake_quick": CommandProfile("Deauth Quick 120", ("--no-pmkid", "--no-wps", "--wpat", "120"), "Fast WPA timeout profile."),
}

# Retained for compatibility with older modules that still import setting.options.
options: list[dict[str, object]] = []

image = None
draw = None
font = None
current_theme = None


def debounce() -> None:
    time.sleep(DEBOUNCE_TIME)


def apply_theme(theme):
    global current_theme
    current_theme = theme


color_themes = [
    {"name": "Default", "background": (30, 30, 30), "text": (255, 255, 255), "highlight": (255, 255, 0), "shadow": (50, 50, 50)},
    {"name": "Cool Blue", "background": (50, 50, 50), "text": (173, 216, 230), "highlight": (0, 255, 255), "shadow": (30, 30, 30)},
    {"name": "Warm Red", "background": (70, 70, 70), "text": (255, 182, 193), "highlight": (255, 69, 0), "shadow": (50, 50, 50)},
    {"name": "Green Forest", "background": (40, 40, 40), "text": (144, 238, 144), "highlight": (34, 139, 34), "shadow": (20, 20, 20)},
    {"name": "Cyberpunk", "background": (20, 20, 20), "text": (0, 255, 0), "highlight": (255, 0, 255), "shadow": (10, 10, 10)},
    {"name": "Monochrome", "background": (10, 10, 10), "text": (200, 200, 200), "highlight": (255, 255, 255), "shadow": (5, 5, 5)},
]
current_theme = color_themes[0]


def _init_ina219():
    try:
        return INA219(addr=0x43)
    except OSError as error:
        logging.warning("INA219 init failed: %s", error)
        return None


ina219 = _init_ina219()

