import os
import threading

import RPi.GPIO as GPIO

import setting
from display import splash_screen
from interface_utils import monitor_buttons
from menu_utils import landing_menu


os.environ["GPG_TTY"] = "/dev/tty1"


def setup_gpio() -> None:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(setting.KEY_UP_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY_DOWN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY_LEFT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY_RIGHT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY_PRESS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(setting.KEY3_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def main() -> None:
    setup_gpio()
    setting.state["stealth_mode_active"].clear()
    splash_screen()

    button_thread = threading.Thread(target=monitor_buttons, daemon=True)
    button_thread.start()

    try:
        landing_menu()
    finally:
        GPIO.cleanup()


if __name__ == "__main__":
    main()
