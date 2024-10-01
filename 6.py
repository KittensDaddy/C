# -*- coding:utf-8 -*-
import LCD_1in44
import os
import time
import RPi.GPIO as GPIO
import subprocess
import threading
from PIL import Image, ImageDraw, ImageFont


ESSIDS = []  # List to store scanned ESSIDs
selected_essid = None  # Variable to store selected ESSID
scanning = False  # State variable to track if scanning is active

OPTIONS_PER_PAGE = 10
DEBOUNCE_TIME = 0.15

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

# Function to display the ESSID selection menu
def essid_selection_menu():
    global selected_essid
    active_idx = 0
    total_options = len(ESSIDS)

    while True:
        draw.rectangle((0, 0, width, height), outline=0, fill=0)  # Clear screen
        for i, essid in enumerate(ESSIDS):
            if i == active_idx:
                draw.text((6, i * 10), f"> {essid}", font=font, fill=(255, 255, 0))  # Highlight active option
            else:
                draw.text((6, i * 10), f"  {essid}", font=font, fill=(255, 255, 255))  # Normal display

        disp.LCD_ShowImage(image, 0, 0)

        if GPIO.input(KEY_UP_PIN) == GPIO.LOW:
            active_idx = (active_idx - 1) % total_options  # Scroll up
            debounce()

        elif GPIO.input(KEY_DOWN_PIN) == GPIO.LOW:
            active_idx = (active_idx + 1) % total_options  # Scroll down
            debounce()

        elif GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:
            selected_essid = ESSIDS[active_idx]  # Set selected ESSID
            display_message(f"Selected: {selected_essid}")
            time.sleep(1)
            break

    # Clear the display before returning to the menu
    disp.LCD_Clear()

# Get available wireless interfaces from iwconfig
def get_wireless_interfaces():
    try:
        interfaces = []
        output = subprocess.check_output(['/usr/sbin/iwconfig'], text=True).splitlines()
        for line in output:
            if "IEEE 802.11" in line:  # This indicates a wireless interface
                interface_name = line.split()[0]
                interfaces.append(interface_name)
        if not interfaces:
            interfaces.append("No wireless interfaces found")
        return interfaces
    except subprocess.CalledProcessError as e:
        print(f"Error running iwconfig: {e}")
        return ["Error fetching interfaces"]
    except Exception as e:
        print(f"Error: {e}")
        return ["Error fetching interfaces"]


# Initialize wireless interfaces
wireless_interfaces = get_wireless_interfaces()
selected_interface = wireless_interfaces[0]

options = [
    {"name": "Scan Time", "state": "Off", "command": "", "values": ["Off"] + [str(i) for i in range(10, 101, 10)]},
    {"name": "LOOP RUN", "state": False, "command": "-inf"},
    {"name": "Select Interface", "state": selected_interface, "command": ""},
    {"name": "Deauth", "state": False, "command": "--no-wps --no-pmkid"},
    {"name": "PIXIE", "state": False, "command": "--wps-only --pixie"},
    {"name": "Deauth+PIXIE", "state": False, "command": "--no-pmkid --pixie"},
    {"name": "PMKID", "state": False, "command": "--no-wps --pmkid"},
    {"name": "No PMKID", "state": False, "command": "--no-pmkid"},
    {"name": "All Band", "state": False, "command": "-ab"},
    {"name": "Show Cracked", "state": False, "command": None},
    {"name": "Run Background", "state": True, "command": None},
    {"name": "Clients Only", "state": False, "command": "--clients-only"},
    {"name": "No Deauths", "state": False, "command": "--nodeauths"},
    {"name": "Deauth sec", "state": "Off", "command": "", "values": ["Off"] + [str(i) for i in range(31)]},
    {"name": "Quit", "state": False, "command": None}
]




# Debounce function to avoid repeated code
def debounce():
    time.sleep(DEBOUNCE_TIME)

# Display feedback on the screen
def display_message(message):
    draw.rectangle((0, 0, width, height), outline=0, fill=0)
    draw.text((10, 10), message, font=font, fill=(255, 255, 255))
    disp.LCD_ShowImage(image, 0, 0)
    time.sleep(2) 

# Handle option toggling and updating command
def toggle_option(selection):
    option = options[selection]

    # Special case for "Select Interface" option
    if option['name'] == "Select Interface":
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
    try:
        with open(filename, 'r') as file:
            content = file.readlines()
    except Exception as e:
        display_message(f"Error reading file: {e}")
        return

    # Clear the screen before displaying new content
    draw.rectangle((0, 0, width, height), outline=0, fill=0)

    # Store lines that contain 'ESSID', 'PIN', or 'PSK'
    filtered_lines = []
    for line in content:
        if 'essid' in line or 'pin' in line or 'psk' in line:
            filtered_lines.append(line.strip())

    # Check if any lines were found
    if not filtered_lines:
        display_message("No relevant data found.")
        return

    # Display the filtered content with scrolling
    start_idx = 0  # Track starting index for scrolling
    while True:
        # Clear the screen and draw the lines to display
        draw.rectangle((0, 0, width, height), outline=0, fill=0)
        for i in range(start_idx, min(start_idx + (height // 10), len(filtered_lines))):
            draw.text((10, (i - start_idx) * 10), filtered_lines[i], font=font, fill=(255, 255, 255))

        disp.LCD_ShowImage(image, 0, 0)

        # Wait for user input to scroll or exit
        if GPIO.input(KEY_UP_PIN) == GPIO.LOW:
            if start_idx > 0:  # Scroll up
                start_idx -= 1
            time.sleep(0.2)  # Debounce

        elif GPIO.input(KEY_DOWN_PIN) == GPIO.LOW:
            if start_idx < len(filtered_lines) - (height // 10):  # Scroll down
                start_idx += 1
            time.sleep(0.2)  # Debounce

        elif GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:  # Exit on any key press
            break

    disp.LCD_Clear()  # Clear the display before returning to the menu


# Function to display messages on the LCD with line length check
def display_message_with_wrap(message, append=False):
    if not append:  # If not appending, clear the screen
        draw.rectangle((0, 0, width, height), outline=0, fill=0)

    # Maximum characters that fit on one line
    max_chars_per_line = width // 10  # Approximation (depends on font size)

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

    # Display the lines
    for i, line in enumerate(displayed_lines):
        if i * 10 >= height:  # Break if we exceed the height of the display
            break
        draw.text((10, i * 10), line.strip(), font=font, fill=(255, 255, 255))

    disp.LCD_ShowImage(image, 0, 0)


import re

def build_and_run_command():
    if any(option["name"] == "Show Cracked" and option["state"] for option in options):
        display_message("Showing: cracked.json")
        display_file_on_lcd("cracked.json")  # Display file content on the LCD
    else:
        command = ["sudo", "wifite", "-mac", "-i", selected_interface]  # Ensure selected_interface is passed

        # Append command options correctly
        for option in options:
            if option["state"] and option.get("command"):
                command.extend(option["command"].split())

        # Display the command being run
        command_str = ' '.join(command)
        display_message_with_wrap(f"Running: {command_str}")

        # Wait for 1 second before clearing the screen
        time.sleep(1)
        disp.LCD_Clear()  # Clear the display before showing output

        # Execute the command and handle the output
        try:
            # Redirect stdout and stderr to avoid IO errors
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            # Initialize a buffer for scrolling output
            output_lines = []

            def read_output():
                while True:
                    output = process.stdout.readline()
                    if output == '' and process.poll() is not None:
                        break
                    if output:
                        output_stripped = output.strip()
                        print(f"Raw Output: {output_stripped}")  # Debugging line to show raw output

                        # Check for "Starting attacks against" message
                        if "Starting attacks against" in output_stripped:
                            essid_match = re.search(r'Starting attacks against (.+?) \((.*?)\)$', output_stripped)
                            if essid_match:
                                mac_address = essid_match.group(1).strip()  # Get the MAC address
                                display_message_with_wrap(f"Attack on {mac_address}")  # Display the MAC address
                                time.sleep(1)

                        # Handle any other output or messages from Wifite
                        matches = re.findall(r'\x1b\[[0-9;]*m.*?Found.*?\x1b\[[0-9;]*m(\d+).*?target\(s\).*?\x1b\[[0-9;]*m(\d+).*?client\(s\)', output_stripped)

                        for match in matches:
                            if match:
                                target_count, client_count = match
                                output_lines.append(f"{target_count} target, {client_count} client")

                                # Clear the display before showing the new message
                                disp.LCD_Clear()
                                display_message_with_wrap(output_lines[-1])  # Display the latest match

                    time.sleep(0.1)  # Control the scrolling speed

            output_thread = threading.Thread(target=read_output)
            output_thread.start()

            process.wait()  # Wait for the process to complete
            output_thread.join()  # Ensure output is read completely

            # Capture stderr output after the command finishes
            stderr_output = process.stderr.read()
            if stderr_output:
                stderr_output_stripped = stderr_output.strip()
                print(stderr_output_stripped)
                display_message_with_wrap(stderr_output_stripped)

        except Exception as e:
            display_message(f"Error: {e}")





# Function to draw the current scroll window of options
def draw_menu(active_idx):
    draw.rectangle((0, 0, width, height), outline=0, fill=0)  # Clear screen

    # Determine the start and end indices for the visible window
    start_idx = max(0, active_idx - OPTIONS_PER_PAGE + 1)  # Show last few options when at the bottom
    end_idx = min(len(options), start_idx + OPTIONS_PER_PAGE)

    # Draw options within the window
    for i in range(start_idx, end_idx):
        option = options[i]

        # Determine state display text
        if isinstance(option["state"], bool):
            state_text = "On" if option["state"] else "Off"
        else:
            state_text = option["state"]

        if i == active_idx:
            draw.text((6, (i - start_idx) * 10), f"> {option['name']}: {state_text}", font=font, fill=(255, 255, 0))  # Highlight active option
        else:
            draw.text((6, (i - start_idx) * 10), f"  {option['name']}: {state_text}", font=font, fill=(255, 255, 255))  # Normal display

    disp.LCD_ShowImage(image, 0, 0)

# Function to create and display a cat drawing
def display_cat_drawing():
    draw.rectangle((0, 0, width, height), outline=0, fill=0)  # Clear screen
    # Draw a simple cat face
    draw.ellipse((40, 20, 80, 60), fill=(255, 255, 255))  # Cat face
    draw.ellipse((50, 30, 70, 50), fill=(0, 0, 0))  # Cat eyes
    draw.polygon([(50, 20), (60, 0), (70, 20)], fill=(255, 255, 255))  # Cat ears
    # Display the drawing
    disp.LCD_ShowImage(image, 0, 0)
    while True:
        if GPIO.input(KEY_PRESS_PIN) == GPIO.LOW:
            break  # Exit the loop on any key press
        time.sleep(0.1)  # Short delay to avoid rapid looping

    # Clear the display before returning to the menu
    disp.LCD_Clear()

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
            if selected_option["name"] == "Quit":
                display_message("Exiting...")
                time.sleep(1)
                disp.LCD_Clear()
                break
            elif selected_option["name"] == "Scan for Wi-Fi":  # New option to start scanning
                scan_wifi_networks()
                stop_wifi_scanning()
                essid_selection_menu()  # After scanning, enter ESSID selection
            else:
                # Toggle option
                toggle_option(active_idx)
            debounce()

        elif GPIO.input(KEY1_PIN) == GPIO.LOW:
            build_and_run_command()
            debounce()

        elif GPIO.input(KEY2_PIN) == GPIO.LOW:  # New condition to display cat drawing
            display_cat_drawing()
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
    menu_loop()  # Start the interactive menu
    GPIO.cleanup()
