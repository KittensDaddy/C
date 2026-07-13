from __future__ import annotations

import shutil
import subprocess
import threading
import time

import setting
from display import (
    display_message,
    display_message_with_wrap,
    disp,
    draw,
    exit_stealth_mode,
    height,
    is_stealth_mode_active,
    read_output_nonblocking,
    width,
)
from interface_utils import get_selected_interface_name


WIFITE_BIN = shutil.which("wifite") or "/usr/sbin/wifite"


def _append_optional_args(command: list[str], toggle_states=None) -> None:
    app_state = setting.app_state

    if app_state.all_band:
        command.append("-ab")
    if app_state.clients_only:
        command.append("--clients-only")
    if app_state.no_deauths:
        command.append("--nodeauths")
    if app_state.scan_time:
        command.extend(["-p", str(app_state.scan_time)])
    if app_state.wps_time:
        command.extend(["--wps-time", str(app_state.wps_time)])
    if app_state.deauth_timeout:
        command.extend(["--wpat", str(app_state.deauth_timeout)])
    if app_state.selected_bssid:
        command.extend(["--bssid", app_state.selected_bssid])
    for essid in app_state.excluded_essids:
        command.extend(["-E", essid])

    if toggle_states:
        if toggle_states.get("bssid"):
            command.extend(["--bssid", str(toggle_states["bssid"])])
        if toggle_states.get("pixie"):
            command.extend(["--wps-only", "--pixie"])
        elif toggle_states.get("deauth"):
            command.extend(["--no-pmkid", "--no-wps"])
        elif toggle_states.get("pmkid"):
            command.extend(["--pmkid", "--no-wps"])


def build_command(profile_key: str | None = None, toggle_states=None, additional_args: str | None = None) -> list[str] | None:
    interface_name = get_selected_interface_name()
    if not interface_name:
        return None

    selected_profile = profile_key or setting.app_state.attack_profile_key
    profile = setting.ATTACK_PROFILES.get(selected_profile, setting.ATTACK_PROFILES["auto"])

    command = ["sudo", WIFITE_BIN, "-mac", "-i", interface_name]
    command.extend(profile.flags)
    _append_optional_args(command, toggle_states=toggle_states)

    if additional_args:
        command.extend(additional_args.split())
    return command


def build_and_run_command(toggle_states=None, option_name=None, additional_args=None):
    while is_stealth_mode_active():
        exit_stealth_mode()
        time.sleep(0.1)

    profile_key = option_name or None
    command = build_command(profile_key=profile_key, toggle_states=toggle_states, additional_args=additional_args)
    if command is None:
        display_message("No interface selected")
        return

    command_str = " ".join(command)
    disp.LCD_Clear_Black()
    display_message_with_wrap(f"Running: {command_str}")
    time.sleep(0.5)
    draw.rectangle((0, 0, width, height), outline=0, fill=0)

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output_lines: list[str] = []
        output_thread = threading.Thread(target=read_output_nonblocking, args=(process, output_lines), daemon=True)
        output_thread.start()

        process.wait()
        output_thread.join(timeout=1)

        stderr_output = process.stderr.read().strip()
        if stderr_output:
            disp.LCD_Clear()
            display_message_with_wrap(f"Error: {stderr_output}")
            time.sleep(1.5)
    except Exception as error:
        display_message(f"Error: {error}")
        time.sleep(2)


def run_current_attack() -> None:
    build_and_run_command(option_name=setting.app_state.attack_profile_key)
