# -*- coding:utf-8 -*-
import LCD_1in44
import os
import time
import RPi.GPIO as GPIO
import subprocess
import threading
import sys
from PIL import Image, ImageDraw, ImageFont
from INA219 import INA219
from display import (display_message, display_message_with_wrap, draw_menu, 
                    display_file_on_lcd, draw_battery_bar, exit_stealth_mode,
                    stealth, splash_screen, display_top)
from setting import (OPTIONS_PER_PAGE, DEBOUNCE_TIME, KEY_UP_PIN, KEY_DOWN_PIN,
                    KEY_LEFT_PIN, KEY_RIGHT_PIN, KEY_PRESS_PIN, KEY1_PIN, 
                    KEY2_PIN, KEY3_PIN, stealth_mode_active, ina219, color_themes)
from interface_utils import get_wireless_interfaces, monitor_buttons
from menu_utils import landing_menu

os.environ["GPG_TTY"] = "/dev/tty1"
#os.system("export GPG_TTY=$(tty)")

ESSIDS = []  # List to store scanned ESSIDs
excluded_essid = []  # List to store excluded ESSIDs
selected_essid = None  # Variable to store selected ESSID
selected_bssid = None
scanning = False  # State variable to track if scanning is active

# Initialize wireless interfaces
wireless_interfaces = get_wireless_interfaces()
if len(wireless_interfaces) > 1:
    selected_interface = wireless_interfaces[1]
else:
    selected_interface = wireless_interfaces[0]

import re
current_essid = ""
current_mac = ""

# Main program loop
if __name__ == "__main__":
    GPIO.setmode(GPIO.BCM)  # Use BCM numbering
    GPIO.setup(KEY_UP_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_DOWN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_PRESS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY3_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Show splash screen
    splash_screen()

    button_thread = threading.Thread(target=monitor_buttons)
    button_thread.daemon = True
    button_thread.start()

    landing_menu()  # Start the landing menu
    GPIO.cleanup()
