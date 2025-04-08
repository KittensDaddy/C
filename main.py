import LCD_1in44
import os
import time
import RPi.GPIO as GPIO
import threading
from setting import (KEY_UP_PIN, KEY_DOWN_PIN, KEY_PRESS_PIN, KEY1_PIN, 
                    KEY2_PIN, KEY3_PIN, KEY_LEFT_PIN, KEY_RIGHT_PIN)
from display import splash_screen
from menu_utils import landing_menu, monitor_buttons
from essid_utils import ESSIDS, excluded_essid, selected_essid, selected_bssid

os.environ["GPG_TTY"] = "/dev/tty1"

if __name__ == "__main__":
    GPIO.setmode(GPIO.BCM)  # Use BCM numbering
    GPIO.setup(KEY_UP_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_DOWN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_PRESS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY3_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_LEFT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_RIGHT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Show splash screen
    splash_screen()

    # Start button monitoring thread
    button_thread = threading.Thread(target=monitor_buttons)
    button_thread.daemon = True
    button_thread.start()

    # Start the landing menu
    landing_menu()
    
    GPIO.cleanup()
