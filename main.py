import LCD_1in44
import os
import time
import RPi.GPIO as GPIO
import threading
import setting
from display import splash_screen, stealth
from menu_utils import landing_menu
from essid_utils import ESSIDS, excluded_essid, selected_essid, selected_bssid

os.environ["GPG_TTY"] = "/dev/tty1"

def monitor_buttons():
    while True:
        if GPIO.input(setting.KEY3_PIN) == GPIO.LOW:
            setting.state["stealth_mode_active"].clear()
            # Safely reboot the system
            os.system("sudo reboot")
            break  # Exit the loop to ensure no further execution
        if GPIO.input(setting.KEY2_PIN) == GPIO.LOW:
            setting.state["stealth_mode_active"].set()  # Use set() to activate stealth mode
            stealth()
        time.sleep(0.05)  # Short delay to avoid rapid looping

if __name__ == "__main__":
    GPIO.setmode(GPIO.BCM)  # Use BCM numbering
    GPIO.setup(setting.KEY_UP_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY_DOWN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY_PRESS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY3_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY_LEFT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY_RIGHT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Ensure stealth mode is initially off
    setting.state["stealth_mode_active"].clear()

    # Show splash screen
    splash_screen()

    # Start button monitoring thread
    button_thread = threading.Thread(target=monitor_buttons)
    button_thread.daemon = True
    button_thread.start()

    # Start the landing menu
    landing_menu()
    
    GPIO.cleanup()
