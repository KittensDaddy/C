import subprocess
import time
import threading
from setting import options, stealth_mode_active
from display import display_message, display_message_with_wrap, disp, draw, width, height, exit_stealth_mode, read_output_nonblocking
from essid_utils import selected_essid, selected_bssid, excluded_essid
from interface_utils import check_monitor_mode_and_enable, selected_interface

def build_and_run_command(option_name=None):
    global selected_interface, excluded_essid

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

    if option_name:
        # Handle specific options
        if option_name == "PIXIE QUICK 30":
            command.extend(["--wps-only", "--pixie", "--wps-time", "30"])
        elif option_name == "DEAUTH QUICK 120":
            command.extend(["--no-pmkid", "--no-wps", "-wpat", "120"])
    else:
        # Add general options
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
            time.sleep(2)
    except Exception as e:
        display_message(f"Error: {e}")
        time.sleep(2)
