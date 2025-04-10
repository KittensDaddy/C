import json
import subprocess
import re
import time
import RPi.GPIO as GPIO
from display import (draw, disp, draw_shadowed_text, draw_battery_bar, display_message, handle_scroll, is_stealth_mode_active, exit_stealth_mode, width, height, font, image)
import setting
from interface_utils import check_monitor_mode_and_disable

def wifi_menu():
    """
    Submenu to manage Wi-Fi actions: scan, show connected Wi-Fi, or toggle interface.
    """
    setting.debounce()
    local_interfaces = get_wireless_interfaces()  # Get all available interfaces
    selected_interface_idx = 0  # Default to the first interface

    active_idx = 0

    while True:
        # Get the connection status for each interface
        interface_statuses = []
        for interface in local_interfaces:
            try:
                # Use iwconfig to get the connected Wi-Fi ESSID
                result = subprocess.run(["/usr/sbin/iwconfig", interface[0]], capture_output=True, text=True)
                connected_wifi = "Not connected"
                for line in result.stdout.splitlines():
                    if "ESSID:" in line:
                        essid = line.split("ESSID:")[1].strip().strip('"')
                        connected_wifi = essid if essid else "Not connected"
                        break
            except Exception:
                connected_wifi = "Error"
            interface_statuses.append(f"{interface[0]}: {connected_wifi}")

        # Menu options
        options = [
            {"name": f"Using: {local_interfaces[selected_interface_idx][0]})", "action": "scan"},
            {"name": "Toggle Interface", "action": "toggle_interface"},
            *[
                {"name": status, "action": "show_status"}
                for status in interface_statuses
            ],
            {"name": "Back", "action": "back"}
        ]

        total_options = len(options)

        # Display the menu
        draw.rectangle((0, 0, width, height), outline=0, fill=(0, 0, 0))
        for i, option in enumerate(options):
            if i == active_idx:
                draw_shadowed_text(draw, f"> {option['name']}", (6, i * 10), font, (255, 255, 0), (50, 50, 50))
            else:
                draw_shadowed_text(draw, f"  {option['name']}", (6, i * 10), font, (255, 255, 255), (50, 50, 50))

        draw_battery_bar()
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_options)

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            selected_option = options[active_idx]["action"]

            if selected_option == "scan":
                scan_wifi(local_interfaces[selected_interface_idx])  # Use the currently selected interface
            elif selected_option == "toggle_interface":
                selected_interface_idx = (selected_interface_idx + 1) % len(local_interfaces)  # Toggle to the next interface
                display_message(f"Selected interface: {local_interfaces[selected_interface_idx][0]}")
                time.sleep(1)
            elif selected_option == "show_status":
                display_message(options[active_idx]["name"])  # Show the status of the selected interface
                time.sleep(1)
            elif selected_option == "back":
                setting.debounce()
                break


def scan_wifi(interface):
    """
    Scan for nearby Wi-Fi networks and display matches.
    """
    # Disable monitor mode before scanning
    check_monitor_mode_and_disable(interface)

    cracked_file = "/home/sun/cracked.json"
    try:
        # Load Wi-Fi credentials from cracked.json
        with open(cracked_file, "r") as file:
            cracked_data = json.load(file)
    except Exception as e:
        display_message(f"Error reading cracked.json: {e}")
        time.sleep(2)
        return

    if not cracked_data:
        display_message("No Wi-Fi credentials found.")
        time.sleep(2)
        return

    # Extract Wi-Fi names and passwords with non-null PSK
    cracked_list = [{"essid": entry["essid"], "password": entry["psk"]} for entry in cracked_data if entry.get("psk")]

    if not cracked_list:
        display_message("No Wi-Fi networks with valid PSK found.")
        time.sleep(2)
        return

    # Scan for nearby Wi-Fi networks
    display_message("Scanning for Wi-Fi networks...")
    try:
        result = subprocess.run(["iwlist", interface[0], "scan"], capture_output=True, text=True)
        lines = result.stdout.splitlines()
    except Exception as e:
        display_message(f"Error scanning Wi-Fi: {e}")
        time.sleep(2)
        return

    # Parse scan results
    nearby_networks = []
    current_essid = None
    current_signal = None
    for line in lines:
        line = line.strip()
        if line.startswith("ESSID:"):
            current_essid = line.split("ESSID:")[1].strip().strip('"')
        elif "Signal level=" in line:
            match = re.search(r"Signal level=(-?\d+)", line)
            if match:
                current_signal = int(match.group(1))
        if current_essid and current_signal is not None:
            nearby_networks.append({"essid": current_essid, "signal": current_signal})
            current_essid = None
            current_signal = None

    # Match nearby networks with cracked.json and sort by signal strength
    matched_networks = [
        {**network, **next((item for item in cracked_list if item["essid"] == network["essid"]), {})}
        for network in nearby_networks if any(item["essid"] == network["essid"] for item in cracked_list)
    ]
    matched_networks.sort(key=lambda x: x["signal"], reverse=True)

    if not matched_networks:
        display_message("No matching networks found.")
        time.sleep(2)
        return

    active_idx = 0
    total_options = len(matched_networks)

    while True:
        # Display matched networks
        draw.rectangle((0, 0, width, height), outline=0, fill=(0, 0, 0))
        start_idx = max(0, active_idx - setting.OPTIONS_PER_PAGE + 1)
        end_idx = min(total_options, start_idx + setting.OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            network = matched_networks[i]
            essid = network["essid"]
            signal = network["signal"]
            password = network.get("password", "N/A")
            display_text = f"{essid} (Signal: {signal} dBm, PSK: {password})"
            if i == active_idx:
                draw_shadowed_text(draw, f"> {display_text}", (6, (i - start_idx) * 10), font, (255, 255, 0), (50, 50, 50))
            else:
                draw_shadowed_text(draw, f"  {display_text}", (6, (i - start_idx) * 10), font, (255, 255, 255), (50, 50, 50))

        draw_battery_bar()
        disp.LCD_ShowImage(image, 0, 0)

        active_idx = handle_scroll(active_idx, total_options)

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            selected_network = matched_networks[active_idx]
            essid = selected_network["essid"]
            password = selected_network["password"]

            # Attempt to connect to the selected Wi-Fi using nmcli
            try:
                if password != "N/A":
                    subprocess.run(
                        ["sudo", "nmcli", "dev", "wifi", "connect", essid, "password", password, "ifname", interface[0], "name", f"temp-{essid}"],
                        check=True
                    )
                else:
                    display_message("No password available.")
                    time.sleep(2)
                display_message(f"Connected to {essid}")
                time.sleep(2)

                # Check internet connection
                try:
                    result = subprocess.run(["ping", "-I", interface[0], "-c", "1", "8.8.8.8"], capture_output=True, text=True)
                    if result.returncode == 0:
                        display_message("Internet: Connected")
                    else:
                        display_message("Internet: Disconnected")
                except Exception as e:
                    display_message(f"Ping Error: {e}")
                time.sleep(2)

            except subprocess.CalledProcessError as e:
                display_message(f"Failed to connect: {e}")
                time.sleep(2)

        elif GPIO.input(setting.KEY1_PIN) == GPIO.LOW:  # Exit on KEY1 press
            setting.debounce()
            break


def show_connected_wifi(interface):
    """
    Show the currently connected Wi-Fi and allow the user to disconnect.
    """
    try:
        result = subprocess.run(["iwgetid", "-r"], capture_output=True, text=True)
        connected_essid = result.stdout.strip()

        if connected_essid:
            display_message(f"Connected to: {connected_essid}")
            time.sleep(2)

            # Ask if the user wants to disconnect
            draw.rectangle((0, 0, width, height), outline=0, fill=(0, 0, 0))
            draw_shadowed_text(draw, "Disconnect?", (6, 10), font, (255, 255, 0), (50, 50, 50))
            draw_shadowed_text(draw, "> Yes", (6, 30), font, (255, 255, 0), (50, 50, 50))
            draw_shadowed_text(draw, "  No", (6, 40), font, (255, 255, 255), (50, 50, 50))
            disp.LCD_ShowImage(image, 0, 0)

            active_idx = 0
            while True:
                active_idx = handle_scroll(active_idx, 2)
                if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
                    if active_idx == 0:  # Yes
                        subprocess.run(["sudo", "nmcli", "device", "disconnect", interface[0]], check=True)
                        display_message("Disconnected.")
                        time.sleep(2)
                    break
        else:
            display_message("No Wi-Fi connected.")
            time.sleep(2)
    except Exception as e:
        display_message(f"Error: {e}")
        time.sleep(2)


def toggle_interface(current_interface):
    """
    Toggle between available wireless interfaces and save the selected interface.
    """
    interfaces = get_wireless_interfaces()
    if not interfaces:
        display_message("No interfaces available.")
        time.sleep(2)
        return current_interface

    # Find the current index and toggle to the next interface
    current_idx = next((i for i, iface in enumerate(interfaces) if iface == current_interface), -1)
    new_idx = (current_idx + 1) % len(interfaces)
    new_interface = interfaces[new_idx]

    # Save the new interface as the selected interface
    current_interface[0] = new_interface[0]
    display_message(f"Using interface: {new_interface[0]}")
    time.sleep(2)
    return new_interface

def get_wireless_interfaces():
    interfaces = []
    retries = 2

    while retries >= 0:
        try:
            # Run iwconfig to get wireless interface details
            output = subprocess.check_output(['/usr/sbin/iwconfig'], text=True)
            
            # Split output into lines and parse each line
            lines = output.splitlines()
            current_interface = None
            
            for line in lines:
                if "IEEE 802.11" in line or "unassociated" in line:  # This line indicates a wireless interface
                    current_interface = line.split()[0]  # Get the interface name
                    # Get driver information using ethtool
                    try:
                        driver_info = subprocess.check_output(['/usr/sbin/ethtool', '-i', current_interface], text=True)
                        driver_name = next((line.split(": ")[1] for line in driver_info.splitlines() if "driver" in line), "Driver not found")
                        interfaces.append((current_interface, driver_name))
                    except subprocess.CalledProcessError as e:
                        interfaces.append((current_interface, "Error fetching driver info"))
                        print(f"Error running ethtool for {current_interface}: {e}")
                elif current_interface and ("Mode:Monitor" in line or "Mode:Managed" in line):
                    # Ensure we only add interfaces that have a valid mode
                    current_interface = None
            
            if interfaces:
                return interfaces
            elif retries > 0:
                print(f"No interfaces found. Retrying in 2 seconds... ({retries} retries left)")
                time.sleep(2)
                retries -= 1
                continue
            else:
                return [("No wireless interfaces found", "")]

        except subprocess.CalledProcessError as e:
            print(f"Error running iwconfig: {e}")
            return [("Error fetching interfaces", "")]
        except Exception as e:
            print(f"Error: {e}")
            return [("Error fetching interfaces", "")]