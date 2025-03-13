import RPi.GPIO as GPIO
from setting import (KEY_UP_PIN, KEY_DOWN_PIN, KEY_PRESS_PIN, KEY1_PIN, KEY2_PIN, 
                    KEY_LEFT_PIN, DEBOUNCE_TIME, stealth_mode_active, color_themes,
                    debounce, apply_theme)
from display import (display_message, display_file_on_lcd, draw_menu, 
                    exit_stealth_mode, stealth, draw_shadowed_text, disp, draw, font,
                    width, height, current_theme, draw_battery_bar)
from wifi_utils import scan_wifi_networks, build_and_run_command, build_and_run_command_with_option
from interface_utils import refresh_interfaces, wireless_interfaces

def handle_scroll(active_idx, total_items):
    if GPIO.input(KEY_UP_PIN) == GPIO.LOW:
        active_idx = (active_idx - 1) % total_items  # Scroll up
        debounce()
    elif GPIO.input(KEY_DOWN_PIN) == GPIO.LOW:
        active_idx = (active_idx + 1) % total_items  # Scroll down
        debounce()
    return active_idx

 #Update the menu loop to include scanning and selecting ESSIDs
def menu_loop():
    debounce()
    active_idx = 0
    total_options = len(options)

    while True:
        draw_menu(active_idx)

        active_idx = handle_scroll(active_idx, total_options)

        if GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:
            selected_option = options[active_idx]  # Correctly define selected_option
            if selected_option.get("run_on_press"):
                display_message("Showing: cracked.json")
                display_file_on_lcd("cracked.json")
            elif selected_option["name"] == "Quit":
                display_message("Exiting...")
                time.sleep(1)
                disp.LCD_Clear()
                break
            elif selected_option["name"] == "Scan WIFI":  # New option to start scanning
                scan_wifi_networks()
                display_message("please select wifi")
                time.sleep(0.3)
                essid_selection_menu()  # After scanning, enter ESSID selection
            elif selected_option["name"] == "Refresh Interfaces":  # New option to refresh interfaces
                refresh_interfaces()
            elif selected_option["name"] == "Scan and Exclude ESSIDs":  # New option to scan and exclude ESSIDs
                scan_and_toggle_essids()
            else:
                # Toggle option
                toggle_option(active_idx)
            debounce()

        elif GPIO.input(KEY1_PIN) == GPIO.LOW:
            build_and_run_command()
            debounce()

        elif GPIO.input(KEY2_PIN) == GPIO.LOW:  # New condition to display cat drawing
            stealth()
            debounce()

        elif GPIO.input(KEY_LEFT_PIN) == GPIO.LOW:  # Go back to landing menu
            landing_menu()
            debounce()

def landing_menu():
    debounce()
    options = [
        {"name": "Wifite", "action": menu_loop},
        {"name": "Setting", "action": setting_menu},
        {"name": "Show Crack", "action": lambda: display_file_on_lcd("cracked.json")},
        {"name": "Select Interface", "action": lambda: toggle_option_by_name("I")},
        {"name": "Refresh Interfaces", "action": refresh_interfaces},
        {"name": "PIXIE QUICK 30", "action": lambda: build_and_run_command_with_option("PIXIE QUICK 30")},
        {"name": "DEAUTH QUICK 120", "action": lambda: build_and_run_command_with_option("DEAUTH QUICK 120")}
    ]
    active_idx = 0
    total_options = len(options)

    while True:
        while stealth_mode_active:
            exit_stealth_mode()
            time.sleep(0.1)  # Short delay to avoid rapid looping
        draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])
        start_idx = max(0, active_idx - OPTIONS_PER_PAGE + 1)
        end_idx = min(total_options, start_idx + OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            option = options[i]
            if i == active_idx:
                draw_shadowed_text(draw, f"> {option['name']}", (6, (i - start_idx) * 10), font, current_theme["highlight"], current_theme["shadow"])  # Highlight active option
            else:
                draw_shadowed_text(draw, f"  {option['name']}", (6, (i - start_idx) * 10), font, current_theme["text"], current_theme["shadow"])

        draw_battery_bar()  # Ensure battery bar is drawn
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_options)

        if GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:
            options[active_idx]["action"]()
            debounce()

        #elif GPIO.input(KEY1_PIN) == GPIO.LOW or GPIO.input(KEY_LEFT_PIN) == GPIO.LOW:  # Exit on KEY1 or KEY_LEFT press
        #    debounce()
        #    break

def setting_menu():
    debounce()
    active_idx = 0
    total_themes = len(color_themes)

    while True:
        while stealth_mode_active:
            exit_stealth_mode()
            time.sleep(0.1)  # Short delay to avoid rapid looping
        draw.rectangle(0, 0, width, height), outline=0, fill(current_theme["background"])
        start_idx = max(0, active_idx - OPTIONS_PER_PAGE + 1)
        end_idx = min(total_themes, start_idx + OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            theme = color_themes[i]
            if i == active_idx:
                draw_shadowed_text(draw, f"> {theme['name']}", (6, (i - start_idx) * 10), font, current_theme["highlight"], current_theme["shadow"])  # Highlight active option
            else:
                draw_shadowed_text(draw, f"  {theme['name']}", (6, (i - start_idx) * 10), font, current_theme["text"], current_theme["shadow"])

        draw_battery_bar()  # Ensure battery bar is drawn
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_themes)

        if GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:
            apply_theme(color_themes[active_idx])
            #display_message(f"Theme set to: {color_themes[active_idx]['name']}")
            debounce()
            break

        elif GPIO.input(KEY1_PIN) == GPIO.LOW or GPIO.input(KEY_LEFT_PIN) == GPIO.LOW:  # Exit on KEY1 or KEY_LEFT press
            break

# Handle option toggling and updating command
def toggle_option(selection):
    option = options[selection]

    # Special case for "Select Interface" option
    if option['name'] == "I":
        try:
            current_index = wireless_interfaces.index(option["state"])
        except ValueError:
            current_index = 0  # Default to the first interface if not found
        option["state"] = wireless_interfaces[(current_index + 1) % len(wireless_interfaces)]
        global selected_interface  # Update the selected interface globally
        selected_interface = option["state"]
        print(f"Selected interface: {selected_interface}")

    # Special case for "Scan Time" option
    elif option['name'] == "Scan Time":
        current_index = option["values"].index(option["state"])
        option["state"] = option["values"][(current_index + 1) % len(option["values"])]
        option["command"] = f"-p {option['state']}"

    # Special case for "WPS Time" option
    elif option['name'] == "WPS Time":
        current_index = option["values"].index(option["state"])
        option["state"] = option["values"][(current_index + 1) % len(option["values"])]
        option["command"] = f"--wps-time {option['state']}" if option["state"] != "Off" else ""

    # Special case for "Deauth sec" option
    elif option['name'] == "Deauth sec":
        current_index = option["values"].index(option["state"])
        option["state"] = option["values"][(current_index + 1) % len(option["values"])]
        option["command"] = f"--wpadt {option['state']}" if option["state"] != "Off" else ""

    else:
        # Toggle state for boolean options
        option["state"] = not option["state"]

def toggle_option_by_name(option_name):
    option = next((opt for opt in options if opt["name"] == option_name), None)
    if option:
        toggle_option(options.index(option))

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

def monitor_buttons():
    global stealth_mode_active
    while True:
        #if GPIO.input(KEY3_PIN) == GPIO.LOW:
        #    print("Restart button pressed. Restarting script...")
        #    time.sleep(0.5)  # Debounce
        #    os.execv(sys.executable, ['python3'] + sys.argv)  # Restart the script

        if GPIO.input(KEY2_PIN) == GPIO.LOW:
            print("Stealth mode activated.")
            time.sleep(0.5)  # Debounce
            stealth_mode_active = True
            stealth()
            stealth_mode_active = False

        time.sleep(0.05)  # Short delay to avoid rapid looping
