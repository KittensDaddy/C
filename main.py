import LCD_1in44
import os
import time
import threading
from setting import (KEY_UP_PIN, KEY_DOWN_PIN, KEY_PRESS_PIN, KEY1_PIN, 
                    KEY2_PIN, KEY3_PIN)
from display import splash_screen
from menu_utils import landing_menu, monitor_buttons
from essid_utils import ESSIDS, excluded_essid, selected_essid, selected_bssid
from gpio_manager import GPIOManager

os.environ["GPG_TTY"] = "/dev/tty1"

# Initialize GPIO manager
gpio_manager = GPIOManager()

if __name__ == "__main__":
    # Set up GPIO pins
    gpio_manager.claim_input(KEY_UP_PIN)
    gpio_manager.claim_input(KEY_DOWN_PIN)
    gpio_manager.claim_input(KEY_PRESS_PIN)
    gpio_manager.claim_input(KEY1_PIN)
    gpio_manager.claim_input(KEY2_PIN)
    gpio_manager.claim_input(KEY3_PIN)

    # Show splash screen
    splash_screen()

    # Start button monitoring thread
    button_thread = threading.Thread(target=monitor_buttons)
    button_thread.daemon = True
    button_thread.start()

    # Start the landing menu
    landing_menu()
    
    # Cleanup GPIO
    gpio_manager.close()
