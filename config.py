# -*- coding: utf-8 -*-
"""Central configuration: pins, paths, presets, defaults.

This module has NO imports from the rest of the project, so any module can
import it without circular-dependency risk.
"""

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
SCROLL_HOLD_TIME = 2.0          # hold a scroll key before it accelerates
SCROLL_FAST_SPEED = 0.03        # seconds between steps when held
SCROLL_SLOW_SPEED = 0.10

# ---------------------------------------------------------------------------
# Files & paths
# ---------------------------------------------------------------------------
PROJECT_DIR = "/home/sun/C"
CRACKED_FILE   = PROJECT_DIR + "/cracked.json"
HANDSHAKE_REMOTE_DIR = "/home/sun/handshake"
LOG_FILE = PROJECT_DIR + "/wifibox.log"

# Wifite binary lookup order
WIFITE_BIN_CANDIDATES = (
    "/usr/sbin/wifite",
    "/usr/local/bin/wifite",
    "/usr/bin/wifite",
)

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
    {"name": "WPS",        "kind": "bool",  "state": True,  "flag": "--no-wps", "invert": True},
    {"name": "Pixie Dust", "kind": "bool",  "state": True,  "flag": "--pixie"},
    {"name": "Null PIN",   "kind": "bool",  "state": True,  "flag": "--no-nullpin", "invert": True},
    {"name": "WPS Tool",   "kind": "cycle", "state": "reaver", "values": ["reaver", "bully"], "flag": None},
    {"name": "Ignore Locks","kind": "bool", "state": False, "flag": "--ignore-locks"},
    {"name": "WPA",        "kind": "bool",  "state": True,  "flag": "--no-wpa", "invert": True},
    {"name": "PMKID",      "kind": "bool",  "state": True,  "flag": "--no-pmkid", "invert": True},
    {"name": "Deauth",     "kind": "bool",  "state": True,  "flag": None},
    {"name": "No Deauth",  "kind": "bool",  "state": False, "flag": "--nodeauths"},
    {"name": "WPA3",       "kind": "bool",  "state": False, "flag": "--wpa3"},
    {"name": "Force SAE",  "kind": "bool",  "state": False, "flag": "--force-sae"},
    {"name": "WEP",        "kind": "bool",  "state": False, "flag": "--wep"},
]

# Timing options (cycle pickers)
DEFAULT_TIMING = [
    {"name": "Scan Time",     "kind": "cycle", "state": "30", "values": [str(i) for i in range(10, 101, 10)]},
    {"name": "WPS Timeout",   "kind": "cycle", "state": "30", "values": ["Off"] + [str(i) for i in range(30, 301, 30)]},
    {"name": "WPA Timeout",   "kind": "cycle", "state": "500", "values": ["Off"] + [str(i) for i in range(60, 901, 60)]},
    {"name": "Deauth Sec",    "kind": "cycle", "state": "10", "values": ["Off"] + [str(i) for i in range(1, 31)]},
    {"name": "PMKID Timeout", "kind": "cycle", "state": "60", "values": ["Off"] + [str(i) for i in range(30, 301, 30)]},
    {"name": "Num Deauths",   "kind": "cycle", "state": "5",  "values": ["Off"] + [str(i) for i in range(1, 21)]},
    {"name": "Loop",          "kind": "bool",  "state": False, "flag": "-inf"},
]

# Target filters
DEFAULT_FILTERS = [
    {"name": "All Bands",     "kind": "bool", "state": False, "flag": "-ab"},
    {"name": "2GHz Only",     "kind": "bool", "state": False, "flag": "-2"},
    {"name": "5GHz Only",     "kind": "bool", "state": False, "flag": "-5"},
    {"name": "Clients Only",  "kind": "bool", "state": False, "flag": "--clients-only"},
    {"name": "Min Signal",    "kind": "cycle", "state": "Off", "values": ["Off"] + [str(i) for i in range(0, -91, -10)], "flag": "--power"},
    {"name": "Max Targets",   "kind": "cycle", "state": "All", "values": ["All", "1", "2", "3", "5", "10"], "flag": "--first"},
    {"name": "Ignore Cracked","kind": "bool", "state": False, "flag": "-ic"},
    {"name": "Ignore Captured","kind": "bool", "state": False, "flag": "--ignore-captured"},
    {"name": "Skip Crack",    "kind": "bool", "state": False, "flag": "--skip-crack"},
]

# Interface options
DEFAULT_INTERFACE_OPTS = [
    {"name": "Random MAC",   "kind": "cycle", "state": "Off", "values": ["Off", "Full", "Vendor"], "flag": "--mac"},
    {"name": "Dual Interface","kind": "bool", "state": False, "flag": "--dual-interface"},
    {"name": "Kill Conflicts","kind": "bool", "state": True,  "flag": "--kill"},
    {"name": "Daemon",       "kind": "bool",  "state": False, "flag": "--daemon"},
    {"name": "Use Hcxdump",  "kind": "bool",  "state": False, "flag": "--hcxdump"},
]

# ---------------------------------------------------------------------------
# Quick-run presets
# ---------------------------------------------------------------------------
PRESETS = [
    {"name": "PIXIE Rush",     "desc": "Fast WPS pixie dust", "args": ["--wps-only", "--pixie", "--wps-time", "30"]},
    {"name": "WPA Grab",       "desc": "Passive handshake",   "args": ["--no-wps", "--no-pmkid", "--nodeauths"]},
    {"name": "PMKID Hunter",   "desc": "PMKID capture",       "args": ["--no-wps", "--pmkid"]},
    {"name": "Full Power",     "desc": "All attacks, all bands", "args": ["-ab"]},
    {"name": "PIXIE Q60",      "desc": "WPS pixie 60s",       "args": ["--wps-only", "--pixie", "--wps-time", "60"]},
    {"name": "Survey Only",    "desc": "No crack, no deauth", "args": ["--skip-crack", "--nodeauths"]},
    {"name": "WPA3 Focus",     "desc": "WPA3 only",           "args": ["--wpa3"]},
]

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
    target_essid = None
    target_bssid = None
    stealth_active = False


# Runtime option containers (mutable lists of dicts)
attack_modes   = [dict(x) for x in DEFAULT_ATTACK_MODES]
timing         = [dict(x) for x in DEFAULT_TIMING]
filters        = [dict(x) for x in DEFAULT_FILTERS]
interface_opts = [dict(x) for x in DEFAULT_INTERFACE_OPTS]
presets        = list(PRESETS)
