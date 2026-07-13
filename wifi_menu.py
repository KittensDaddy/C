from __future__ import annotations

import json
import shutil
import subprocess

import setting
from display import display_message
from interface_utils import check_monitor_mode_and_disable, get_selected_interface_name
from menu_core import MenuItem, run_action_menu, select_from_list


IW_BIN = shutil.which("iw") or "/usr/sbin/iw"
NMCLI_BIN = shutil.which("nmcli") or "/usr/bin/nmcli"
PING_BIN = shutil.which("ping") or "/usr/bin/ping"
IWGETID_BIN = shutil.which("iwgetid") or "/usr/sbin/iwgetid"


def _load_cracked_networks() -> list[dict[str, str]]:
    try:
        with setting.CRACKED_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as error:
        display_message(f"cracked.json error: {error}")
        return []

    networks = []
    for item in data:
        essid = item.get("essid")
        password = item.get("psk")
        if essid and password:
            networks.append({"essid": essid, "password": password})
    return networks


def _scan_nearby_networks(interface_name: str) -> list[dict[str, int | str]]:
    check_monitor_mode_and_disable(interface_name)
    result = subprocess.run(["sudo", IW_BIN, "dev", interface_name, "scan"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        display_message("Wi-Fi scan failed")
        return []

    networks: list[dict[str, int | str]] = []
    current_bssid = None
    current_essid = None
    current_signal = None

    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("BSS "):
            current_bssid = stripped.split()[1].split("(")[0]
            current_essid = None
            current_signal = None
        elif stripped.startswith("SSID:"):
            current_essid = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("signal:"):
            try:
                current_signal = int(float(stripped.split(":", 1)[1].split()[0]))
            except ValueError:
                current_signal = None

        if current_bssid and current_essid and current_signal is not None:
            networks.append({"essid": current_essid, "bssid": current_bssid, "signal": current_signal})
            current_bssid = None
            current_essid = None
            current_signal = None

    return networks


def _connect_known_network() -> None:
    interface_name = get_selected_interface_name()
    if not interface_name:
        display_message("No interface selected")
        return

    cracked = {item["essid"]: item["password"] for item in _load_cracked_networks()}
    if not cracked:
        display_message("No saved PSKs")
        return

    nearby = [
        network
        for network in _scan_nearby_networks(interface_name)
        if str(network["essid"]) in cracked
    ]
    nearby.sort(key=lambda entry: int(entry["signal"]), reverse=True)

    if not nearby:
        display_message("No known Wi-Fi nearby")
        return

    selected_index = select_from_list(
        nearby,
        label_getter=lambda entry: f"{entry['essid']} ({entry['signal']} dBm)",
        title_lines=["Known Networks"],
    )
    if selected_index is None:
        return

    selected = nearby[selected_index]
    essid = str(selected["essid"])
    password = cracked[essid]
    result = subprocess.run(
        [
            "sudo",
            NMCLI_BIN,
            "dev",
            "wifi",
            "connect",
            essid,
            "password",
            password,
            "ifname",
            interface_name,
            "name",
            f"temp-{essid}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        display_message("nmcli connect failed")
        return

    ping = subprocess.run([PING_BIN, "-I", interface_name, "-c", "1", "8.8.8.8"], capture_output=True, text=True, check=False)
    if ping.returncode == 0:
        display_message(f"Connected: {essid}")
    else:
        display_message(f"Connected: {essid} no net")


def _show_connection_status() -> None:
    interface_name = get_selected_interface_name()
    if not interface_name:
        display_message("No interface selected")
        return

    result = subprocess.run([IWGETID_BIN, interface_name, "--raw"], capture_output=True, text=True, check=False)
    essid = result.stdout.strip()
    display_message(f"Connected: {essid or 'none'}")


def _disconnect_current() -> None:
    interface_name = get_selected_interface_name()
    if not interface_name:
        display_message("No interface selected")
        return
    result = subprocess.run(["sudo", NMCLI_BIN, "device", "disconnect", interface_name], capture_output=True, text=True, check=False)
    if result.returncode == 0:
        display_message("Wi-Fi disconnected")
    else:
        display_message("Disconnect failed")


def wifi_menu():
    items = [
        MenuItem(lambda: f"Interface: {get_selected_interface_name() or 'none'}"),
        MenuItem("Connect Known Network", _connect_known_network),
        MenuItem("Show Connection", _show_connection_status),
        MenuItem("Disconnect", _disconnect_current),
        MenuItem("Back", lambda: "back"),
    ]
    run_action_menu(items, title_lines=["Wi-Fi"])