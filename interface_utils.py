from __future__ import annotations

import shutil
import subprocess
import time

import RPi.GPIO as GPIO

import setting


IW_BIN = shutil.which("iw") or "/usr/sbin/iw"
IP_BIN = shutil.which("ip") or "/usr/sbin/ip"
ETHTOOL_BIN = shutil.which("ethtool") or "/usr/sbin/ethtool"

wireless_interfaces: list[tuple[str, str]] = []
selected_interface: tuple[str, str] | None = None


def _driver_name(interface_name: str) -> str:
    result = subprocess.run([ETHTOOL_BIN, "-i", interface_name], capture_output=True, text=True, check=False)
    for line in result.stdout.splitlines():
        if line.startswith("driver:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def _normalize_interface(interface: tuple[str, str] | str | None) -> str | None:
    if interface is None:
        return None
    if isinstance(interface, tuple):
        return interface[0]
    return interface


def _preferred_interface(interfaces: list[tuple[str, str]]) -> tuple[str, str] | None:
    if not interfaces:
        return None
    for interface in interfaces:
        if interface[1] != "brcmfmac":
            return interface
    return interfaces[0]


def get_wireless_interfaces() -> list[tuple[str, str]]:
    result = subprocess.run([IW_BIN, "dev"], capture_output=True, text=True, check=False)
    interfaces: list[tuple[str, str]] = []

    if result.returncode == 0:
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Interface "):
                interface_name = stripped.split()[1]
                interfaces.append((interface_name, _driver_name(interface_name)))

    if interfaces:
        return interfaces

    fallback = subprocess.run([shutil.which("iwconfig") or "/usr/sbin/iwconfig"], capture_output=True, text=True, check=False)
    for line in fallback.stdout.splitlines():
        if "IEEE 802.11" in line:
            interface_name = line.split()[0]
            interfaces.append((interface_name, _driver_name(interface_name)))
    return interfaces


def refresh_wireless_interfaces() -> list[tuple[str, str]]:
    global wireless_interfaces, selected_interface

    wireless_interfaces = get_wireless_interfaces()
    setting.app_state.wireless_interfaces = list(wireless_interfaces)

    if setting.app_state.selected_interface in wireless_interfaces:
        selected_interface = setting.app_state.selected_interface
    else:
        selected_interface = _preferred_interface(wireless_interfaces)

    setting.app_state.selected_interface = selected_interface
    return wireless_interfaces


def get_selected_interface() -> tuple[str, str] | None:
    if setting.app_state.selected_interface is None:
        refresh_wireless_interfaces()
    return setting.app_state.selected_interface


def get_selected_interface_name() -> str | None:
    selected = get_selected_interface()
    return _normalize_interface(selected)


def cycle_selected_interface() -> tuple[str, str] | None:
    global selected_interface
    interfaces = refresh_wireless_interfaces()
    if not interfaces:
        selected_interface = None
        setting.app_state.selected_interface = None
        return None

    current = get_selected_interface()
    if current not in interfaces:
        selected_interface = interfaces[0]
    else:
        current_index = interfaces.index(current)
        selected_interface = interfaces[(current_index + 1) % len(interfaces)]

    setting.app_state.selected_interface = selected_interface
    return selected_interface


def set_interface_mode(interface: tuple[str, str] | str | None, mode: str) -> bool:
    interface_name = _normalize_interface(interface) or get_selected_interface_name()
    if not interface_name:
        return False

    commands = [
        ["sudo", IP_BIN, "link", "set", interface_name, "down"],
        ["sudo", IW_BIN, "dev", interface_name, "set", "type", mode],
        ["sudo", IP_BIN, "link", "set", interface_name, "up"],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return False
    return True


def check_monitor_mode_and_enable(interface: tuple[str, str] | str | None = None) -> bool:
    return set_interface_mode(interface, "monitor")


def check_monitor_mode_and_disable(interface: tuple[str, str] | str | None = None) -> bool:
    return set_interface_mode(interface, "managed")


def monitor_buttons() -> None:
    from display import exit_stealth_mode, stealth

    while True:
        if GPIO.input(setting.KEY2_PIN) == GPIO.LOW:
            stealth()
            setting.debounce()
        elif GPIO.input(setting.KEY3_PIN) == GPIO.LOW and setting.state["stealth_mode_active"].is_set():
            setting.state["stealth_mode_active"].clear()
            exit_stealth_mode()
            setting.debounce()
        time.sleep(0.05)


refresh_wireless_interfaces()
