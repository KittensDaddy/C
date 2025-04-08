import subprocess
import time
import threading
import re
from setting import stealth_mode_active, options
from display import (display_message, display_message_with_wrap, exit_stealth_mode,
                    display_top, disp, draw, width, height, is_stealth_mode_active, read_output_nonblocking)
from essid_utils import ESSIDS, excluded_essid, selected_essid, selected_bssid
from interface_utils import check_monitor_mode_and_enable, check_monitor_mode_and_disable
from command_utils import build_and_run_command, build_and_run_command_with_option

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
        time.sleep(5)
"""
#Function scan for wifi
def scan_wifi_networks():
    global ESSIDS, current_index
    ESSIDS.clear()  # Clear previous scan results

# Get the scan duration from the "Scan Time" option
    scan_time_option = next(option for option in options if option["name"] == "Scan Time")
    scan_duration = int(scan_time_option["state"])

    display_message(f"Scanning for {scan_duration} seconds...")
    selected_interface_name = selected_interface[0] if isinstance(selected_interface, tuple) else selected_interface
    display_message_with_wrap(f"{selected_interface}, {selected_interface_name}")

    start_time = time.time()
    
    while time.time() - start_time < scan_duration:
        check_monitor_mode_and_disable(selected_interface_name)
        result = subprocess.run(["/usr/sbin/iwlist", selected_interface_name, "scan"], capture_output=True, text=True)

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
"""
def build_and_run_command():
    global selected_interface, excluded_essid  # Ensure excluded_essid is referenced correctly

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

        # Ensure excluded_essid is a list
        if not isinstance(excluded_essid, list):
            excluded_essid = []

        # Add excluded ESSIDs
        for essid in excluded_essid:
            command.append(f'-E{essid}')

        # Add scan time if specified
        scan_time_option = next((option for option in options if option["name"] == "Scan Time"), None)
        if scan_time_option and scan_time_option["state"]:
            command.append(f'-p {scan_time_option["state"]}')

        command_str = ' '.join(command)
        disp.LCD_Clear_Black()
        display_message_with_wrap(f"Running: {command_str}")
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
                time.sleep(0.3)
        except Exception as e:
            display_message(f"Error: {e}")
            time.sleep(2)

def build_and_run_command_with_option(option_name):
    global selected_interface, excluded_essid  # Ensure excluded_essid is referenced correctly

    selected_interface_name = selected_interface[0] if isinstance(selected_interface, tuple) else selected_interface

    command = ["sudo", "/usr/sbin/wifite", "-mac", "-i", selected_interface_name]

    for option in options:
        if option["name"] == option_name and option.get("command"):
            command.extend(option["command"].split())

    if selected_essid:
        command.append(f'-bssid "{selected_bssid}"')

    # Ensure excluded_essid is a list
    if not isinstance(excluded_essid, list):
        excluded_essid = []

    # Add excluded ESSIDs
    for essid in excluded_essid:
        command.append(f'-E{essid}')

    # Add scan time if specified
    scan_time_option = next((option for option in options if option["name"] == "Scan Time"), None)
    if scan_time_option and scan_time_option["state"]:
        command.append(f'-p {scan_time_option["state"]}')

    command_str = ' '.join(command)
    disp.LCD_Clear_Black()
    display_message_with_wrap(f"Running: {command_str}")
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