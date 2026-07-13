import time
import threading
from INA219 import INA219
from PIL import ImageFont, Image, ImageDraw
import LCD_1in44
import logging

# Constants
OPTIONS_PER_PAGE = 11
DEBOUNCE_TIME = 0.15

# Pin definitions
KEY_UP_PIN = 6
KEY_DOWN_PIN = 19
KEY_LEFT_PIN = 5
KEY_RIGHT_PIN = 26
KEY_PRESS_PIN = 13
KEY1_PIN = 21
KEY2_PIN = 20
KEY3_PIN = 16

current_index = 0

state = {
    "stealth_mode_active": threading.Event()  # Replace boolean with threading.Event
}

# Initialize INA219 sensor
try:
    ina219 = INA219(addr=0x43)
except OSError as e:
    # Allow running without INA219 hardware connected.
    logging.warning("INA219 initialization failed (%s). Battery readings disabled.", e)
    ina219 = None

# Initialize LCD dimensions
#width = LCD_1in44.LCD_WIDTH
#height = LCD_1in44.LCD_HEIGHT

# Default values for image and drawing
image = None
draw = None
font = None

def debounce():
    """
    A simple debounce function to prevent multiple readings from a single button press.
    """
    time.sleep(DEBOUNCE_TIME)

def apply_theme(theme):
    """
    Applies a color theme to the display.

    Args:
        theme (dict): A dictionary containing color definitions for background, text, highlight, and shadow.
    """
    global current_theme, image, draw, font, width, height
    current_theme = theme

# Color themes
color_themes = [
    {"name": "Default", "background": (30, 30, 30), "text": (255, 255, 255), "highlight": (255, 255, 0), "shadow": (50, 50, 50)},
    {"name": "Cool Blue", "background": (50, 50, 50), "text": (173, 216, 230), "highlight": (0, 255, 255), "shadow": (30, 30, 30)},
    {"name": "Warm Red", "background": (70, 70, 70), "text": (255, 182, 193), "highlight": (255, 69, 0), "shadow": (50, 50, 50)},
    {"name": "Green Forest", "background": (40, 40, 40), "text": (144, 238, 144), "highlight": (34, 139, 34), "shadow": (20, 20, 20)},
    {"name": "Cyberpunk", "background": (20, 20, 20), "text": (0, 255, 0), "highlight": (255, 0, 255), "shadow": (10, 10, 10)},
    {"name": "Monochrome", "background": (10, 10, 10), "text": (200, 200, 200), "highlight": (255, 255, 255), "shadow": (5, 5, 5)}
]

# Define menu options
options = [
    {"name": "Scan and Exclude ESSIDs", "state": [], "command": None},
    {"name": "Scan WIFI", "state": False, "command": None},
    {"name": "Scan Time", "state": "10", "command": "", "values": [str(i) for i in range(10, 101, 10)]},
    {"name": "LOOP RUN", "state": False, "command": "-inf"},
    {"name": "Deauth", "state": False, "command": "--no-wps --no-pmkid"},
    {"name": "PIXIE", "state": False, "command": "--wps-only --pixie"},
    {"name": "WPS Time", "state": "Off", "command": "", "values": ["Off"] + [str(i) for i in range(60, 300, 10)]},
    {"name": "Deauth+PIXIE", "state": False, "command": "--no-pmkid --pixie"},
    {"name": "PMKID", "state": False, "command": "--no-wps --pmkid"},
    {"name": "No PMKID", "state": False, "command": "--no-pmkid"},
    {"name": "All Band", "state": False, "command": "-ab"},
    {"name": "Show Cracked", "command": None, "run_on_press": True},
    {"name": "Clients Only", "state": False, "command": "--clients-only"},
    {"name": "No Deauths", "state": False, "command": "--nodeauths"},
    {"name": "Deauth sec", "state": "Off", "command": "", "values": ["Off"] + [str(i) for i in range(31)]},
    {"name": "PIXIE QUICK 60", "state": False, "command": "--wps-only --pixie --wps-time 60"},
    {"name": "DEAUTH QUICK 240", "state": False, "command": "--no-pmkid --no-wps -wpat 240"},
    {"name": "Quit", "state": False, "command": None}
]

