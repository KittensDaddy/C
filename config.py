# -*- coding: utf-8 -*-
"""Central configuration: pins, paths, presets, defaults.

This module has NO imports from the rest of the project, so any module can
import it without circular-dependency risk.
"""
import re

# ---------------------------------------------------------------------------
# Hardware paths / pins
# ---------------------------------------------------------------------------
# GPIO (BCM numbering). Joystick uses UP/DOWN/LEFT/RIGHT + PRESS, plus 3 keys.
KEY_UP_PIN     = 6
KEY_DOWN_PIN   = 19
KEY_LEFT_PIN   = 5
KEY_RIGHT_PIN  = 26
KEY_PRESS_PIN  = 13
KEY1_PIN       = 21
KEY2_PIN       = 20
KEY3_PIN       = 16

# LCD (SPI) pins - passed to LCD_1in44
LCD_RST_PIN    = 27
LCD_DC_PIN     = 25
LCD_BL_PIN     = 24

# INA219 battery sensor addresses to probe in order (bus 1)
INA219_BUS     = 1
INA219_ADDRS   = (0x43, 0x40, 0x41, 0x44, 0x45)

# ---------------------------------------------------------------------------
# LCD screen geometry (128x128)
# ---------------------------------------------------------------------------
WIDTH  = 128
HEIGHT = 128

# Visible text rows below the header (default font ~ 10px / 6px glyphs)
OPTIONS_PER_PAGE = 10
MAX_CHARS_PER_LINE = WIDTH // 6   # 21 chars at default font

DEBOUNCE_TIME = 0.15
INTERACTIVE_SCAN_SECONDS = 8    # short scan for the target-picker (not the -p attack value)
SCROLL_HOLD_TIME = 2.0          # hold a scroll key before it accelerates
SCROLL_FAST_SPEED = 0.03        # seconds between steps when held
SCROLL_SLOW_SPEED = 0.10
# Auto-repeat feel for held navigation keys (action keys never repeat).
REPEAT_DELAY = 0.45            # hold this long before auto-repeat starts
REPEAT_RATE  = 0.22           # seconds between repeats once it starts

# ---------------------------------------------------------------------------
# Files & paths — runtime data lives in ~/.local/share/wifi-box/
# (migrated from PROJECT_DIR on first run if old files exist)
# ---------------------------------------------------------------------------
PROJECT_DIR = "/home/sun/C"
DATA_DIR      = "/home/sun/.local/share/wifi-box"
CRACKED_FILE   = DATA_DIR + "/cracked.json"
SETTINGS_FILE  = DATA_DIR + "/settings.json"
LEGACY_CRACKED = PROJECT_DIR + "/cracked.json"       # migrate → DATA_DIR
LEGACY_UPLOAD   = PROJECT_DIR + "/upload_state.json"  # migrate → DATA_DIR
HANDSHAKE_REMOTE_DIR = "/home/sun/handshake"
UPLOAD_STATE_FILE = DATA_DIR + "/upload_state.json"
LOG_FILE = PROJECT_DIR + "/wifibox.log"

# Native engine: where captured handshakes / PMKID hashes are written, and the
# wordlist used for on-box cracking (manual only — never auto-run).
CAPTURE_DIR   = DATA_DIR + "/captures"
WORDLIST_PATH = "/usr/share/wordlists/rockyou.txt"

# ---------------------------------------------------------------------------
# IP camera module — discovery + credential/unauth/vuln probing (read-only).
# ---------------------------------------------------------------------------
CAMERAS_FILE      = DATA_DIR + "/cameras.json"
CAMERA_CREDS_FILE = DATA_DIR + "/camera_creds.txt"   # optional user drop-in list
CAMERA_SAVE_DIR   = DATA_DIR + "/cameras"            # snapshots + config dumps

# TCP connect timeout (s) for the port scan; HTTP/RTSP probe timeout (s).
CAMERA_SCAN_TIMEOUT = 0.6
CAMERA_HTTP_TIMEOUT = 5
CAMERA_MAX_THREADS  = 64

# Ports to probe per host: HTTP(S) web, RTSP, ONVIF/ISAPI + common brand ports.
CAMERA_PORTS = [80, 443, 554, 8000, 8080, 8899, 37777, 34567, 9527, 5000,
                9000, 10554, 34599, 37778, 10080, 1024]

# Server for uploads (reached over tailscale)
UPLOAD_SERVER = "100.124.251.39"
UPLOAD_USER   = "sun"
# scp/rsync SSH options for non-interactive operation
UPLOAD_SCP_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

# ---------------------------------------------------------------------------
# Interface handling
# ---------------------------------------------------------------------------
# Internal wifi driver to leave alone (attacks use external USB card)
INTERNAL_DRIVER = "brcmfmac"
INTERNAL_NAME   = "wl0"

# ---------------------------------------------------------------------------
# Color themes: name -> (background, text, highlight, shadow, accent)
# accent is used for progress/check glyphs.
# ---------------------------------------------------------------------------
COLOR_THEMES = [
    {"name": "Default",    "background": (30, 30, 30),  "text": (255, 255, 255), "highlight": (255, 255, 0), "shadow": (50, 50, 50),  "accent": (0, 255, 0)},
    {"name": "Cool Blue",  "background": (30, 40, 55),  "text": (173, 216, 230), "highlight": (0, 255, 255), "shadow": (20, 30, 40),  "accent": (0, 255, 0)},
    {"name": "Warm Red",   "background": (50, 30, 30),  "text": (255, 182, 193), "highlight": (255, 69, 0),  "shadow": (30, 15, 15),  "accent": (0, 255, 0)},
    {"name": "Green",      "background": (25, 40, 25),  "text": (144, 238, 144), "highlight": (255, 255, 0), "shadow": (12, 20, 12),  "accent": (0, 255, 0)},
    {"name": "Cyberpunk",  "background": (20, 20, 40),  "text": (0, 255, 0),     "highlight": (255, 0, 255), "shadow": (8, 8, 20),   "accent": (0, 255, 0)},
    {"name": "Mono",       "background": (10, 10, 10),  "text": (200, 200, 200), "highlight": (255, 255, 255), "shadow": (5, 5, 5),  "accent": (255, 255, 255)},
]

# Battery color thresholds (percent)
BATTERY_HIGH = 50   # >=  -> white
BATTERY_MID  = 30   # >=  -> orange, else red

# ---------------------------------------------------------------------------
# Attack mode defaults. state is the live value; values is a cycle list for
# picker options; circle flags whether the option renders as an ON/OFF dot.
# kind: "bool" | "cycle"
# ---------------------------------------------------------------------------
DEFAULT_ATTACK_MODES = [
    {"name": "WPA",         "kind": "bool",  "state": True},   # capture handshake
    {"name": "WPS Pixie",   "kind": "bool",  "state": True},   # reaver -K pixie-dust
    {"name": "WPS Tool",    "kind": "cycle", "state": "reaver", "values": ["reaver", "bully"]},
    {"name": "Deauth",      "kind": "bool",  "state": True},   # deauth clients during WPA capture
    {"name": "Ignore Locks","kind": "bool",  "state": False},  # keep going when the AP WPS-locks
    {"name": "PMKID",       "kind": "bool",  "state": False},  # optional hcxdumptool PMKID
]

# Timing options — fed straight to the engine (seconds). "Off" disables where noted.
DEFAULT_TIMING = [
    {"name": "Scan Time",   "kind": "cycle", "state": "30",  "values": [str(i) for i in range(10, 101, 10)]},
    {"name": "WPS Timeout", "kind": "cycle", "state": "180", "values": [str(i) for i in range(30, 361, 30)]},
    {"name": "WPA Timeout", "kind": "cycle", "state": "300", "values": [str(i) for i in range(60, 901, 60)]},
    {"name": "Deauth Sec",  "kind": "cycle", "state": "10",  "values": ["Off"] + [str(i) for i in range(2, 31, 2)]},
    {"name": "Num Deauths", "kind": "cycle", "state": "5",   "values": [str(i) for i in range(1, 21)]},
]

# Target filters
DEFAULT_FILTERS = [
    {"name": "Band",          "kind": "cycle", "state": "Both", "values": ["Both", "2.4", "5"]},
    {"name": "Min Signal",    "kind": "cycle", "state": "Off",  "values": ["Off"] + [str(i) for i in range(-30, -91, -10)]},
    {"name": "Max Targets",   "kind": "cycle", "state": "All",  "values": ["All", "1", "2", "3", "5", "10"]},
    {"name": "Ignore Cracked","kind": "bool",  "state": False},
]

# ---------------------------------------------------------------------------
# Hardcoded NEVER-attack APs — skipped by BSSID (primary) and ESSID (fallback).
# Sources: /home/sun/root/handshakes potfile + pcaps, wifi/hs Tofu cap 2026-02.
# ---------------------------------------------------------------------------
EXCLUDED_TARGETS = [
    {"bssid": "64:20:E0:2E:43:93", "essid": "LDGARMENT_2.4G"},
    {"bssid": "64:20:E0:2E:43:97", "essid": "LDGARMENT_5G"},
    {"bssid": "C0:25:2F:F0:F4:BE", "essid": "BABYGIRL"},
    {"bssid": "66:20:E1:1D:A7:5E", "essid": "Tofu_2.4G"},   # newer (2026-02)
    {"bssid": "7A:53:0D:1B:7E:39", "essid": "Tofu_2.4G"},   # older potfile
]

# Exact ESSID excludes (any case).
EXCLUDED_ESSIDS = {
    "LDGARMENT_2.4G", "LDGARMENT_5G", "LDGARMENT24G", "LDGARMENT5G",
    "LDGARMENT_24G", "LDGARMENT_2.4", "LDGARMENT_5",
    "BABYGIRL",
    "Tofu_2.4G", "Tofu_5G", "Tofu24G", "Tofu5G",
    "TOFU_2.4G", "TOFU_5G", "TOFU24G", "TOFU5G",
}

# Prefix match — catches renames like LDGARMENT24GHz / Tofu_5GHz.
EXCLUDED_ESSID_PREFIXES = (
    "LDGARMENT",
    "BABYGIRL",
    "TOFU",
)


def _norm_bssid(bssid):
    """Normalize to AA:BB:CC:DD:EE:FF (upper). Accepts -, :, or bare hex."""
    if not bssid:
        return ""
    raw = re.sub(r"[^0-9A-Fa-f]", "", str(bssid))
    if len(raw) != 12:
        return str(bssid).replace("-", ":").upper()
    parts = [raw[i:i + 2] for i in range(0, 12, 2)]
    return ":".join(parts).upper()


def excluded_bssids():
    return {_norm_bssid(t["bssid"]) for t in EXCLUDED_TARGETS}


def _essid_excluded(essid):
    if not essid:
        return False
    name = essid.strip()
    upper = name.upper()
    if upper in {e.upper() for e in EXCLUDED_ESSIDS}:
        return True
    # Strip separators so LDGARMENT_2.4G / LDGARMENT24G both match prefix.
    compact = re.sub(r"[^A-Z0-9]", "", upper)
    for prefix in EXCLUDED_ESSID_PREFIXES:
        p = prefix.upper()
        if upper.startswith(p) or compact.startswith(p):
            return True
    return False


def is_excluded_net(net):
    """True if this AP must never be attacked (by MAC or ESSID)."""
    if _norm_bssid(net.get("bssid")) in excluded_bssids():
        return True
    return _essid_excluded(net.get("essid"))


def apply_hardcoded_excludes():
    """Merge hardcoded BSSIDs into Runtime so every path (UI + engine) skips them."""
    have = {_norm_bssid(b) for b in Runtime.excluded_bssids}
    for b in excluded_bssids():
        if b and b not in have:
            Runtime.excluded_bssids.append(b)
            have.add(b)
    have_e = {e.upper() for e in Runtime.excluded_essids}
    for e in EXCLUDED_ESSIDS:
        if e.upper() not in have_e:
            Runtime.excluded_essids.append(e)
            have_e.add(e.upper())


# ---------------------------------------------------------------------------
# Quick-run presets — self-contained attack recipes.
# ---------------------------------------------------------------------------
# A preset overrides config for that run only (see orchestrator._PRESET_MAP).
# Optional keys:
#   attacks      list subset of {"wps","wpa","pmkid"} (run in that order per target)
#   scan         seconds to discover targets before attacking (Quick Attack)
#   wps_time     WPS pixie timeout (s)        wpa_time  WPA/PMKID listen window (s)
#   deauth       True/False                   num_deauths / deauth_sec  deauth rounds
#   tool         "reaver" | "bully"           ignore_locks  True/False
#   band         "Both" | "2.4" | "5"         max_targets   int | "All"
PRESETS = [
    # Clientless — grab a PMKID with no deauth (works without connected clients).
    {"name": "PMKID Snag",       "desc": "Clientless PMKID, no deauth",
     "attacks": ["pmkid"], "deauth": False, "wpa_time": 60, "scan": 15},
    # Aggressive — WPA handshake, hammer clients off to force a reconnect.
    {"name": "Handshake Hunter", "desc": "WPA + heavy deauth",
     "attacks": ["wpa"], "deauth": True, "num_deauths": 12, "deauth_sec": 5,
     "wpa_time": 240, "scan": 20},
    # Combined — try clientless PMKID first, then deauth for a handshake.
    {"name": "WPA Blitz",        "desc": "PMKID then deauth HS",
     "attacks": ["pmkid", "wpa"], "deauth": True, "num_deauths": 10,
     "wpa_time": 180, "scan": 20},
    # WPS pixie-dust — Rush is the aggressive adaptive profile (20s/AP).
    {"name": "PIXIE Rush",
     "desc": "20s/AP adaptive pixie",
     "attacks": ["wps"],
     "pixie": True,
     "wps_time": 20,
     "scan": 10,
     "tool": "reaver",
     "ignore_locks": True,
     "band": "Both",
     "max_targets": "All"},
    {"name": "WPS + WPA",        "desc": "Pixie then handshake",
     "attacks": ["wps", "wpa"], "pixie": True, "wps_time": 60, "deauth": True,
     "scan": 20},
    # Recon only.
    {"name": "Survey Only",      "desc": "Scan, no attack",
     "attacks": [], "scan": 30},
]


# ---------------------------------------------------------------------------
# Option accessors — read a value out of a config snapshot (list of dicts) by
# option name. Pure functions so the engine can read a frozen AttackRequest.
# ---------------------------------------------------------------------------
def opt_state(container, name, default=None):
    for o in container:
        if o.get("name") == name:
            return o.get("state", default)
    return default


def opt_bool(container, name, default=False):
    return bool(opt_state(container, name, default))


def opt_int(container, name, default):
    val = opt_state(container, name, None)
    # Deauth Sec "Off" must disable interval deauth (not fall back to default).
    if val == "Off" and name == "Deauth Sec":
        return 0
    if val in (None, "Off", "All"):
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

# ---------------------------------------------------------------------------
# Runtime state (mutable singletons)
# ---------------------------------------------------------------------------
class Runtime:
    """Live state shared across modules. Initialized in main.py."""
    selected_interface = None      # external attack interface tuple (name, driver)
    internal_interface = None      # internal wifi name (wl0)
    all_interfaces = []            # list of (name, driver)
    monitor_iface = None           # interface currently in monitor mode
    current_essid = None
    current_bssid = None
    excluded_essids = []
    excluded_bssids = []           # BSSID-level exclusions (native multi-exclude)
    target_essid = None
    target_bssid = None
    target_channel = None          # channel of the picked target (speeds -c lock)
    target_bssids = []             # multi-select include list (Scan & Attack)
    last_scan = []                 # most recent scan result (list of net dicts)
    stealth_active = False


# Safety cap (seconds) for confirming a single picked target is on air before
# attacking it (bounds the pre-attack airodump lock if the target isn't seen).
TARGETED_SCAN_CAP = 30


# Runtime option containers (mutable lists of dicts)
attack_modes   = [dict(x) for x in DEFAULT_ATTACK_MODES]
timing         = [dict(x) for x in DEFAULT_TIMING]
filters        = [dict(x) for x in DEFAULT_FILTERS]
presets        = list(PRESETS)


# ---------------------------------------------------------------------------
# RaspberryPi hardware class (used by LCD_1in44.py for SPI + GPIO)
# Uses shared lgpio via hardware.gpio to avoid chip conflict with buttons.
# ---------------------------------------------------------------------------
import time as _time
import logging

try:
    import spidev as _spidev
except ImportError:
    _spidev = None

from hardware import gpio as _gpio


class RaspberryPi:
    """GPIO/SPI hardware layer for LCD_1in44.py. Uses shared GPIO module."""

    def __init__(self, spi=None, spi_freq=40000000,
                 rst=27, dc=25, bl=24, bl_freq=1000,
                 i2c=None, i2c_freq=100000):
        self.INPUT = False
        self.OUTPUT = True
        self.SPEED = spi_freq
        self.BL_freq = bl_freq

        _gpio.init()
        self.GPIO_RST_PIN = _gpio.Pin(rst, mode_output=True, initial=False)
        self.GPIO_DC_PIN = _gpio.Pin(dc, mode_output=True, initial=False)
        self.GPIO_BL_PIN = _gpio.Pin(bl, mode_output=True, initial=False)

        if spi is None and _spidev is not None:
            try:
                spi = _spidev.SpiDev(0, 0)
            except Exception:
                spi = None
        self.SPI = spi
        if self.SPI is not None:
            self.SPI.max_speed_hz = spi_freq
            self.SPI.mode = 0b00

    def gpio_mode(self, Pin, Mode, pull_up=None, active_state=True):
        return _gpio.Pin(Pin, mode_output=bool(Mode))

    def digital_write(self, Pin, value):
        Pin.on() if value else Pin.off()

    def digital_read(self, Pin):
        return Pin.value

    def delay_ms(self, delaytime):
        _gpio.delay_ms(delaytime)

    def gpio_pwm(self, Pin):
        return _gpio.Pin(Pin, mode_output=True)  # backlight on/off

    def spi_writebyte(self, data):
        if self.SPI is not None:
            self.SPI.writebytes(data)

    def bl_DutyCycle(self, duty):
        self.GPIO_BL_PIN.value = 1 if duty > 0 else 0

    def bl_Frequency(self, freq):
        self.BL_freq = freq

    def module_init(self):
        if self.SPI is not None:
            self.SPI.max_speed_hz = self.SPEED
            self.SPI.mode = 0b00
        return 0

    def module_exit(self):
        logging.debug("spi end")
        if self.SPI is not None:
            self.SPI.close()
        logging.debug("gpio cleanup...")
        self.digital_write(self.GPIO_RST_PIN, 1)
        self.digital_write(self.GPIO_DC_PIN, 0)
        self.GPIO_BL_PIN.close()
        _time.sleep(0.001)
