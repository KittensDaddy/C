import subprocess
import time
import threading
from setting import options, state
from display import display_message, display_message_with_wrap, disp, draw, width, height, exit_stealth_mode, read_output_nonblocking, is_stealth_mode_active
from essid_utils import selected_essid, selected_bssid, excluded_essid
from interface_utils import check_monitor_mode_and_enable, selected_interface

def build_and_run_command(toggle_states=None, option_name=None, additional_args=None):
    while is_stealth_mode_active():
            exit_stealth_mode()
            time.sleep(0.1)

    global selected_interface, excluded_essid, selected_essid

    if selected_interface is None:
        display_message("No interface selected!")
        return

    # Handle tuple interface name
    if isinstance(selected_interface, tuple):
        selected_interface_name = selected_interface[0]
    else:
        selected_interface_name = str(selected_interface)

    if not selected_interface_name:
        display_message("Invalid interface!")
        return

    command = ["sudo", "/usr/sbin/wifite", "-mac", "-i", str(selected_interface_name)]

    if excluded_essid:
        for essid in excluded_essid:
            command.append(f'-E{essid}')
            
    if option_name:
        # Handle specific options
        if option_name == "PIXIE QUICK 30":
            command.extend(["--wps-only", "--pixie", "--wps-time", "30"])
        elif option_name == "DEAUTH QUICK 120":
            command.extend(["--no-pmkid", "--no-wps", "-wpat", "120"])
    else:
        # Add toggle states from specific_attack_menu
        if toggle_states:
            if not toggle_states.get("pmkid", True):
                command.append("--no-pmkid")
            if toggle_states.get("pixie") and not toggle_states.get("deauth"):
                command.extend(["-ab", "--wps-only", "--pixie", "--bssid", toggle_states["bssid"]])  # Use a list
            elif toggle_states.get("deauth") and not toggle_states.get("pixie"):
                command.extend(["-ab", "--no-wps", "--bssid", toggle_states["bssid"]])  # Use a list
            elif toggle_states.get("pixie") and toggle_states.get("deauth"):
                command.extend(["-ab", "--pixie", "--bssid", toggle_states["bssid"]])  # Use a list

    # Add additional arguments
    if additional_args:
        command.extend(additional_args.split())

    # Add scan time if specified
    scan_time_option = next((option for option in options if option["name"] == "Scan Time"), None)
    if scan_time_option and scan_time_option["state"] and not toggle_states:
        command.append(f'-p {scan_time_option["state"]}')

    command_str = ' '.join(command)
    disp.LCD_Clear_Black()
    display_message_with_wrap(f"Running: {command_str}")
    time.sleep(1)
    draw.rectangle((0, 0, width, height), outline=0, fill=0)
    print("Running command:", command, command_str)
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

    while state["stealth_mode_active"].is_set():
        exit_stealth_mode()
        time.sleep(0.1)  # Short delay to avoid rapid looping
