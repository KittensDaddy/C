import subprocess
import time
import RPi.GPIO as GPIO
from setting import KEY2_PIN, stealth_mode_active, options
from display import display_message, draw_menu
selected_interface = None

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

# Initialize wireless interfaces
wireless_interfaces = get_wireless_interfaces()
if len(wireless_interfaces) > 1:
    selected_interface = wireless_interfaces[1]
else:
    selected_interface = wireless_interfaces[0]

def check_monitor_mode_and_enable(interface):
    interface_name = interface[0] if isinstance(interface, tuple) else interface
    
    try:
        # Check for interfaces in monitor mode
        result = subprocess.run(["/usr/sbin/iwconfig"], capture_output=True, text=True)
        monitor_mode_found = False

        # Check if any interface is in monitor mode
        for line in result.stdout.splitlines():
            if "Mode:Monitor" in line:
                monitor_mode_found = True
                break
        
        # If no monitor mode found, start airmon-ng on the selected interface
        if not monitor_mode_found:
            print(f"No interfaces in monitor mode. Starting airmon-ng on {interface_name}...")
            subprocess.run(["sudo", "/usr/sbin/airmon-ng", "start", interface_name], check=True)
            print(f"Started monitor mode on {interface_name}.")
        else:
            print("An interface is already in monitor mode.")
    
    except subprocess.CalledProcessError as e:
        print(f"Error during operation: {e}")
        time.sleep(5)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        time.sleep(5)
        
def check_monitor_mode_and_disable(interface):
    interface_name = interface[0] if isinstance(interface, tuple) else interface
    
    try:
        # Check for interfaces in monitor mode
        result = subprocess.run(["/usr/sbin/iwconfig"], capture_output=True, text=True)
        monitor_mode_found = False

        # Check if any interface is in monitor mode
        for line in result.stdout.splitlines():
            if "Mode:Monitor" in line:
                monitor_mode_found = True
                subprocess.run(["sudo", "ip", "link", "set", interface_name, "down"], check=True)
                subprocess.run(["sudo", "iwconfig", interface_name, "mode", "managed"], check=True)
                subprocess.run(["sudo", "ip", "link", "set", interface_name, "up"], check=True)

    except subprocess.CalledProcessError as e:
        print(f"Error during operation: {e}")
        time.sleep(5)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        time.sleep (5)
