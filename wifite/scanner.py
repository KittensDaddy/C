# -*- coding: utf-8 -*-
"""WiFi scanning with smart fallbacks.

Returns a list of dicts:
  {"essid", "bssid", "signal", "channel", "enc"}
"""
import subprocess
import re
import time

import config
from wifite import interface as iface_mod


def run(cmd, timeout=25):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def _parse_iw(stdout):
    """Parse `iw dev <if> scan` output."""
    nets = []
    cur = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("BSS "):
            if cur:
                nets.append(cur)
            m = re.search(r"BSS (([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})", line)
            cur = {"bssid": m.group(1) if m else None,
                   "essid": None, "signal": None, "channel": None, "enc": None}
        elif cur is not None:
            m = re.search(r"signal:\s*([-0-9]+)", line)
            if m:
                cur["signal"] = int(m.group(1))
            m = re.search(r'SSID:\s*(.*)', line)
            if m:
                cur["essid"] = m.group(1).strip() or None
            m = re.search(r"DS Parameter set: channel (\d+)", line)
            if m:
                cur["channel"] = m.group(1)
            if "WPA" in line:
                cur["enc"] = "WPA"
            elif "WEP" in line:
                cur["enc"] = "WEP"
            elif cur["enc"] is None and "Group cipher" in line:
                cur["enc"] = "OPEN"
    if cur:
        nets.append(cur)
    return [n for n in nets if n.get("essid")]


def _parse_iwlist(stdout):
    """Fallback parser for `iwlist <if> scan`."""
    nets = []
    cur = None
    for line in stdout.splitlines():
        line = line.strip()
        if "Cell " in line and "Address:" in line:
            if cur:
                nets.append(cur)
            m = re.search(r"Address:\s*(([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})",
                          line)
            cur = {"bssid": m.group(1) if m else None,
                   "essid": None, "signal": None, "channel": None, "enc": None}
        elif cur is not None:
            m = re.search(r'ESSID:\s*"(.*)"', line)
            if m:
                cur["essid"] = m.group(1) or None
            m = re.search(r"Signal level=([-0-9]+)", line)
            if m:
                cur["signal"] = int(m.group(1))
            m = re.search(r"Frequency:[\d.]+\s*GHz \(Channel (\d+)\)", line)
            if m:
                cur["channel"] = m.group(1)
            if "WPA" in line:
                cur["enc"] = "WPA"
    if cur:
        nets.append(cur)
    return [n for n in nets if n.get("essid")]


def scan(ifname, duration=None, progress_cb=None, stop_flag=None):
    """Scan for networks. Returns list of net dicts."""
    iface_name = iface_mod.iface_name(ifname)
    # Make sure interface is in managed mode for scanning
    try:
        if iface_mod.is_in_monitor(iface_name):
            iface_mod.disable_monitor(iface_name)
    except Exception:  # noqa: BLE001
        pass

    nets = []
    end = time.time() + (duration or 20)

    while time.time() < end:
        if stop_flag and stop_flag.is_set():
            break
        # Try `iw` first
        out = run(["iw", "dev", iface_name, "scan"])
        parsed = _parse_iw(out.stdout) if out else []
        if not parsed:
            # Fallback: iwlist
            out2 = run(["/usr/sbin/iwlist", iface_name, "scan"])
            parsed = _parse_iwlist(out2.stdout) if out2 else []
        if not parsed:
            # Fallback: nmcli
            out3 = run(["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL", "dev",
                        "wifi", "list"])
            if out3:
                parsed = _parse_nmcli(out3.stdout)

        merged = {n["bssid"]: n for n in nets}
        for n in parsed:
            merged[n["bssid"]] = n
        nets = list(merged.values())

        if progress_cb:
            progress_cb(len(nets))

        time.sleep(0.4)
        # don't rescan too fast; give the kernel a moment
        time.sleep(1.6)

    # de-dup by bssid one final time
    seen = {}
    for n in nets:
        seen[n["bssid"]] = n
    return list(seen.values())


def _parse_nmcli(stdout):
    """Parse nmcli -t output. BSSID has 6 colon-separated hex bytes."""
    nets = []
    bssid_re = re.compile(r"((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}):(-?\d+)")
    for line in stdout.splitlines():
        m = bssid_re.search(line)
        if not m:
            continue
        bssid = m.group(1).upper()
        try:
            dbm = int(m.group(2).split("\\")[0]) * -1
        except (ValueError, TypeError):
            dbm = 0
        ssid = line[:m.start()].rstrip(":").replace("\\:", ":")
        ssid = re.sub(r"\\$", "", ssid)  # trailing escape from split on colon
        nets.append({"essid": ssid, "bssid": bssid,
                     "signal": dbm, "channel": None, "enc": None})
    return nets


def _is_hex_byte(s):
    try:
        int(s.strip(), 16)
        return len(s.strip()) == 2
    except (ValueError, TypeError):
        return False


def sort_by_signal(nets):
    return sorted(nets, key=lambda n: n.get("signal") or 0, reverse=True)
