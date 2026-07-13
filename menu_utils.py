from __future__ import annotations

import shutil
import subprocess

import display
import setting
from command_utils import build_and_run_command, run_current_attack
from display import display_file_on_lcd, display_message
from interface_utils import cycle_selected_interface, get_selected_interface_name, refresh_wireless_interfaces
from menu_core import MenuItem, run_action_menu, select_from_list
from wifi_menu import wifi_menu


IW_BIN = shutil.which("iw") or "/usr/sbin/iw"


def _profile_label() -> str:
    profile = setting.ATTACK_PROFILES[setting.app_state.attack_profile_key]
    return f"Profile: {profile.name}"


def _target_label() -> str:
    return f"Target: {setting.app_state.selected_essid or 'any'}"


def _interface_label() -> str:
    selected = get_selected_interface_name()
    return f"Interface: {selected or 'none'}"


def _scan_targets() -> list[tuple[str, str]]:
    interface_name = get_selected_interface_name()
    if not interface_name:
        display_message("No interface selected")
        return []

    display_message("Scanning targets...")
    result = subprocess.run(["sudo", IW_BIN, "dev", interface_name, "scan"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        display_message("Target scan failed")
        return []

    found: list[tuple[str, str]] = []
    current_bssid = None
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("BSS "):
            current_bssid = stripped.split()[1].split("(")[0]
        elif stripped.startswith("SSID:"):
            essid = stripped.split(":", 1)[1].strip()
            if essid and current_bssid and (essid, current_bssid) not in found:
                found.append((essid, current_bssid))

    setting.app_state.essids = found
    return found


def _select_target() -> None:
    networks = _scan_targets()
    if not networks:
        display_message("No targets found")
        return

    selected_index = select_from_list(
        networks,
        label_getter=lambda entry: entry[0],
        title_lines=["Select Target"],
    )
    if selected_index is None:
        return

    selected_essid, selected_bssid = networks[selected_index]
    setting.app_state.selected_essid = selected_essid
    setting.app_state.selected_bssid = selected_bssid
    display_message(f"Target: {selected_essid}")


def _exclude_targets() -> None:
    networks = _scan_targets()
    if not networks:
        display_message("No targets found")
        return

    while True:
        selected_index = select_from_list(
            networks,
            label_getter=lambda entry: entry[0],
            title_lines=["Exclude Targets", "Press left to back"],
            color_getter=lambda entry, is_active: (
                (0, 255, 0)
                if entry[0] in setting.app_state.excluded_essids
                else display.current_theme["highlight"] if is_active else display.current_theme["text"]
            ),
        )
        if selected_index is None:
            return

        essid = networks[selected_index][0]
        if essid in setting.app_state.excluded_essids:
            setting.app_state.excluded_essids.remove(essid)
        else:
            setting.app_state.excluded_essids.append(essid)


def _cycle_attack_profile() -> None:
    keys = list(setting.ATTACK_PROFILES.keys())
    current_index = keys.index(setting.app_state.attack_profile_key)
    setting.app_state.attack_profile_key = keys[(current_index + 1) % len(keys)]


def _toggle(attr_name: str) -> None:
    current_value = getattr(setting.app_state, attr_name)
    setattr(setting.app_state, attr_name, not current_value)


def _cycle_numeric(attr_name: str, values: list[int | None]) -> None:
    current_value = getattr(setting.app_state, attr_name)
    current_index = values.index(current_value)
    setattr(setting.app_state, attr_name, values[(current_index + 1) % len(values)])


def _theme_menu() -> None:
    selected_index = select_from_list(
        setting.color_themes,
        label_getter=lambda theme: theme["name"],
        title_lines=["Themes"],
    )
    if selected_index is None:
        return

    selected_theme = setting.color_themes[selected_index]
    setting.apply_theme(selected_theme)
    display.current_theme = selected_theme
    display_message(f"Theme: {selected_theme['name']}")


def _cycle_interface() -> None:
    selected = cycle_selected_interface()
    if selected is None:
        display_message("No interface found")
        return
    display_message(f"Using {selected[0]}")


def _refresh_interfaces() -> None:
    refresh_wireless_interfaces()
    selected = get_selected_interface_name()
    display_message(f"Refreshed: {selected or 'none'}")


def _show_cracked() -> None:
    display_file_on_lcd(str(setting.CRACKED_FILE))


def _wifite_menu() -> None:
    items = [
        MenuItem(_profile_label, _cycle_attack_profile),
        MenuItem(_target_label, _select_target),
        MenuItem("Exclude Targets", _exclude_targets),
        MenuItem(lambda: f"Scan Time: {setting.app_state.scan_time}s", lambda: _cycle_numeric("scan_time", [10, 20, 30, 40, 50, 60, 90, 120])),
        MenuItem(lambda: f"WPS Time: {setting.app_state.wps_time or 'off'}", lambda: _cycle_numeric("wps_time", [None, 30, 60, 90, 120, 180])),
        MenuItem(lambda: f"Deauth Wait: {setting.app_state.deauth_timeout or 'off'}", lambda: _cycle_numeric("deauth_timeout", [None, 60, 120, 180, 240])),
        MenuItem(lambda: f"All Band: {'on' if setting.app_state.all_band else 'off'}", lambda: _toggle("all_band")),
        MenuItem(lambda: f"Clients Only: {'on' if setting.app_state.clients_only else 'off'}", lambda: _toggle("clients_only")),
        MenuItem(lambda: f"No Deauths: {'on' if setting.app_state.no_deauths else 'off'}", lambda: _toggle("no_deauths")),
        MenuItem("Run Current Attack", run_current_attack),
        MenuItem("Quick Pixie 30", lambda: build_and_run_command(option_name="pixie_quick")),
        MenuItem("Quick Deauth 120", lambda: build_and_run_command(option_name="handshake_quick")),
        MenuItem("Back", lambda: "back"),
    ]
    run_action_menu(items, title_lines=["Wifite"], quick_action=run_current_attack)


def landing_menu():
    refresh_wireless_interfaces()
    items = [
        MenuItem("Attack Menu", _wifite_menu),
        MenuItem(_interface_label, _cycle_interface),
        MenuItem("Refresh Interfaces", _refresh_interfaces),
        MenuItem("Saved Wi-Fi", wifi_menu),
        MenuItem("Show Cracked", _show_cracked),
        MenuItem("Theme", _theme_menu),
        MenuItem("Quit", lambda: "back"),
    ]
    run_action_menu(items, title_lines=["Main Menu"])import RPi.GPIO as GPIO
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
    from __future__ import annotations

    import shutil
    import subprocess

    import display
    import setting
    from command_utils import build_and_run_command, run_current_attack
    from display import display_file_on_lcd, display_message
    from interface_utils import cycle_selected_interface, get_selected_interface_name, refresh_wireless_interfaces
    from menu_core import MenuItem, run_action_menu, select_from_list
    from wifi_menu import wifi_menu


    IW_BIN = shutil.which("iw") or "/usr/sbin/iw"


    def _profile_label() -> str:
        profile = setting.ATTACK_PROFILES[setting.app_state.attack_profile_key]
        return f"Profile: {profile.name}"


    def _target_label() -> str:
        return f"Target: {setting.app_state.selected_essid or 'any'}"


    def _interface_label() -> str:
        selected = get_selected_interface_name()
        return f"Interface: {selected or 'none'}"


    def _scan_targets() -> list[tuple[str, str]]:
        interface_name = get_selected_interface_name()
        if not interface_name:
            display_message("No interface selected")
            return []

        display_message("Scanning targets...")
        result = subprocess.run(["sudo", IW_BIN, "dev", interface_name, "scan"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            display_message("Target scan failed")
            return []

        found: list[tuple[str, str]] = []
        current_bssid = None
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("BSS "):
                current_bssid = stripped.split()[1].split("(")[0]
            elif stripped.startswith("SSID:"):
                essid = stripped.split(":", 1)[1].strip()
                if essid and current_bssid and (essid, current_bssid) not in found:
                    found.append((essid, current_bssid))

        setting.app_state.essids = found
        return found


    def _select_target() -> None:
        networks = _scan_targets()
        if not networks:
            display_message("No targets found")
            return

        selected_index = select_from_list(
            networks,
            label_getter=lambda entry: entry[0],
            title_lines=["Select Target"],
        )
        if selected_index is None:
            return

        selected_essid, selected_bssid = networks[selected_index]
        setting.app_state.selected_essid = selected_essid
        setting.app_state.selected_bssid = selected_bssid
        display_message(f"Target: {selected_essid}")


    def _exclude_targets() -> None:
        networks = _scan_targets()
        if not networks:
            display_message("No targets found")
            return

        while True:
            selected_index = select_from_list(
                networks,
                label_getter=lambda entry: entry[0],
                title_lines=["Exclude Targets", "Press left to back"],
                color_getter=lambda entry, is_active: (
                    (0, 255, 0)
                    if entry[0] in setting.app_state.excluded_essids
                    else display.current_theme["highlight"] if is_active else display.current_theme["text"]
                ),
            )
            if selected_index is None:
                return

            essid = networks[selected_index][0]
            if essid in setting.app_state.excluded_essids:
                setting.app_state.excluded_essids.remove(essid)
            else:
                setting.app_state.excluded_essids.append(essid)


    def _cycle_attack_profile() -> None:
        keys = list(setting.ATTACK_PROFILES.keys())
        current_index = keys.index(setting.app_state.attack_profile_key)
        setting.app_state.attack_profile_key = keys[(current_index + 1) % len(keys)]


    def _toggle(attr_name: str) -> None:
        current_value = getattr(setting.app_state, attr_name)
        setattr(setting.app_state, attr_name, not current_value)


    def _cycle_numeric(attr_name: str, values: list[int | None]) -> None:
        current_value = getattr(setting.app_state, attr_name)
        current_index = values.index(current_value)
        setattr(setting.app_state, attr_name, values[(current_index + 1) % len(values)])


    def _theme_menu() -> None:
        selected_index = select_from_list(
            setting.color_themes,
            label_getter=lambda theme: theme["name"],
            title_lines=["Themes"],
        )
        if selected_index is None:
            return
        selected_theme = setting.color_themes[selected_index]
        setting.apply_theme(selected_theme)
        display.current_theme = selected_theme
        display_message(f"Theme: {selected_theme['name']}")


    def _cycle_interface() -> None:
        selected = cycle_selected_interface()
        if selected is None:
            display_message("No interface found")
            return
        display_message(f"Using {selected[0]}")


    def _refresh_interfaces() -> None:
        refresh_wireless_interfaces()
        selected = get_selected_interface_name()
        display_message(f"Refreshed: {selected or 'none'}")


    def _show_cracked() -> None:
        display_file_on_lcd(str(setting.CRACKED_FILE))


    def _wifite_menu() -> None:
        items = [
            MenuItem(_profile_label, _cycle_attack_profile),
            MenuItem(_target_label, _select_target),
            MenuItem("Exclude Targets", _exclude_targets),
            MenuItem(lambda: f"Scan Time: {setting.app_state.scan_time}s", lambda: _cycle_numeric("scan_time", [10, 20, 30, 40, 50, 60, 90, 120])),
            MenuItem(lambda: f"WPS Time: {setting.app_state.wps_time or 'off'}", lambda: _cycle_numeric("wps_time", [None, 30, 60, 90, 120, 180])),
            MenuItem(lambda: f"Deauth Wait: {setting.app_state.deauth_timeout or 'off'}", lambda: _cycle_numeric("deauth_timeout", [None, 60, 120, 180, 240])),
            MenuItem(lambda: f"All Band: {'on' if setting.app_state.all_band else 'off'}", lambda: _toggle("all_band")),
            MenuItem(lambda: f"Clients Only: {'on' if setting.app_state.clients_only else 'off'}", lambda: _toggle("clients_only")),
            MenuItem(lambda: f"No Deauths: {'on' if setting.app_state.no_deauths else 'off'}", lambda: _toggle("no_deauths")),
            MenuItem("Run Current Attack", run_current_attack),
            MenuItem("Quick Pixie 30", lambda: build_and_run_command(option_name="pixie_quick")),
            MenuItem("Quick Deauth 120", lambda: build_and_run_command(option_name="handshake_quick")),
            MenuItem("Back", lambda: "back"),
        ]
        run_action_menu(items, title_lines=["Wifite"], quick_action=run_current_attack)


    def landing_menu():
        refresh_wireless_interfaces()
        items = [
            MenuItem("Attack Menu", _wifite_menu),
            MenuItem(_interface_label, _cycle_interface),
            MenuItem("Refresh Interfaces", _refresh_interfaces),
            MenuItem("Saved Wi-Fi", wifi_menu),
            MenuItem("Show Cracked", _show_cracked),
            MenuItem("Theme", _theme_menu),
            MenuItem("Quit", lambda: "back"),
        ]
        run_action_menu(items, title_lines=["Main Menu"])
