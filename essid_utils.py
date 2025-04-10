import time
import RPi.GPIO as GPIO
from setting import (KEY_PRESS_PIN, KEY1_PIN, OPTIONS_PER_PAGE, 
                    state, debounce)
from display import (display_message, exit_stealth_mode, draw, disp, font, 
                    width, height, draw_battery_bar, image, handle_scroll)

ESSIDS = []  # List to store scanned ESSIDs
excluded_essid = []  # List to store excluded ESSIDs
selected_essid = None  # Variable to store selected ESSID
selected_bssid = None

def essid_selection_menu_for_exclusion():
    global ESSIDS, excluded_essid

    while is_stealth_mode_active():
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping

    if not ESSIDS:
        display_message("No Wi-Fi networks to display")
        return
    
    active_idx = 0
    total_essids = len(ESSIDS)

    while True:
        if state["stealth_mode_active"].is_set():  # Ensure stealth mode is respected
            exit_stealth_mode()
            time.sleep(0.1)  # Short delay to avoid rapid looping
        draw.rectangle((0, 0, width, height), outline=0, fill=0)
        start_idx = max(0, active_idx - OPTIONS_PER_PAGE + 1)
        end_idx = min(total_essids, start_idx + OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            essid, bssid = ESSIDS[i]  # Unpacking to get both ESSID and BSSID
            if i == active_idx:
                color = (0, 255, 0) if essid in excluded_essid else (255, 255, 0)  # Green if excluded, yellow otherwise
                draw.text((6, (i - start_idx) * 10), f"> {essid}", font=font, fill=color)  # Highlight active option
            else:
                color = (0, 255, 0) if essid in excluded_essid else (255, 255, 255)  # Green if excluded, white otherwise
                draw.text((6, (i - start_idx) * 10), f"  {essid}", font=font, fill=color)

        draw_battery_bar()  # Ensure battery bar is drawn
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_essids)

        if GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:
            essid, bssid = ESSIDS[active_idx]  # Store both selected values
            if essid in excluded_essid:
                excluded_essid.remove(essid)
            else:
                excluded_essid.append(essid)
            time.sleep(0.1)

        elif GPIO.input(KEY1_PIN) == GPIO.LOW:  # Exit on KEY1 press
            debounce()
            break
        time.sleep(0.1)