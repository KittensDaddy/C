import subprocess
import time
import RPi.GPIO as GPIO
from setting import KEY2_PIN, stealth_mode_active
from display import display_message, draw_menu, stealth
from menu_utils import options

def get_wireless_interfaces():
    interfaces = []

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
                    if driver_name != "brcmfmac":  # Exclude interfaces with brcmfmac driver
                        interfaces.append((current_interface, driver_name))
                except subprocess.CalledProcessError as e:
                    interfaces.append((current_interface, "Error fetching driver info"))
                    print(f"Error running ethtool for {current_interface}: {e}")
            elif current_interface and ("Mode:Monitor" in line or "Mode:Managed" in line):
                # Ensure we only add interfaces that have a valid mode
                current_interface = None
        if not interfaces:
            interfaces.append(("No wireless interfaces found", ""))
        
        return interfaces

    except subprocess.CalledProcessError as e:
        print(f"Error running iwconfig: {e}")
        return [("Error fetching interfaces", "")]
    except Exception as e:
        print(f"Error: {e}")
        return [("Error fetching interfaces", "")]

def refresh_interfaces():
    global wireless_interfaces, selected_interface
    wireless_interfaces.clear()  # Clear old interfaces
    wireless_interfaces = get_wireless_interfaces()
    
    if len(wireless_interfaces) > 1:
        selected_interface = wireless_interfaces[1]
    else:
        selected_interface = wireless_interfaces[0]
    
    # Update the "I" option with the new selected interface
    for option in options:
        if option["name"] == "I":
            option["state"] = selected_interface
    
    display_message("Interfaces refreshed")
    draw_menu(current_index)  # Redraw the menu to reflect the changes

# Initialize wireless interfaces
wireless_interfaces = get_wireless_interfaces()
if len(wireless_interfaces) > 1:
    selected_interface = wireless_interfaces[1]
else:
    selected_interface = wireless_interfaces[0]
