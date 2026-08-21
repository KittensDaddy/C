# -*- coding: utf-8 -*-
"""WiFi scanning with smart fallbacks.

Returns a list of dicts:
  {"essid", "bssid", "signal", "channel", "enc"}
"""
import subprocess
import re
import time
import shutil

import config
from attack import interface as iface_mod


def _tool(name, *fallbacks):
    """Resolve a tool to an absolute path (systemd PATH often lacks /usr/sbin)."""
    return shutil.which(name) or next(
        (p for p in fallbacks if _exists(p)), name)


def _exists(path):
    import os
    return os.path.exists(path)


IW = _tool("iw", "/usr/sbin/iw", "/sbin/iw")
IWLIST = _tool("iwlist", "/usr/sbin/iwlist", "/sbin/iwlist")
NMCLI = _tool("nmcli", "/usr/bin/nmcli")
IP = _tool("ip", "/usr/sbin/ip", "/sbin/ip", "/bin/ip")


def _log(msg):
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write("[scan] %s\n" % msg)
    except Exception:  # noqa: BLE001
        pass


def run(cmd, timeout=25):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception as e:  # noqa: BLE001
        _log("run failed %s: %s" % (cmd[:2], e))
        return None


def _freq_to_channel(freq_mhz):
    """Map MHz center frequency to 802.11 channel number, or None."""
    try:
        f = int(freq_mhz)
    except (ValueError, TypeError):
        return None
    if 2412 <= f <= 2472:
        return str((f - 2407) // 5)
    if f == 2484:
        return "14"
    if 5180 <= f <= 5885:
        return str((f - 5000) // 5)
    if 5955 <= f <= 7115:          # 6 GHz (approx)
        return str((f - 5950) // 5)
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
                   "essid": None, "signal": None, "channel": None,
                   "enc": None, "wps": None}
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
            # Many kernels only print freq — derive channel when DS IE missing.
            if cur.get("channel") is None:
                m = re.search(r"freq:\s*(\d+)", line)
                if m:
                    cur["channel"] = _freq_to_channel(m.group(1))
            # WPS IE present → prefer these for pixie (unknown stays None).
            if re.search(r"\bWPS:\b|Wi-?Fi Protected Setup", line, re.I):
                cur["wps"] = True
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
                   "essid": None, "signal": None, "channel": None,
                   "enc": None, "wps": None}
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
    """Scan for networks. Returns list of net dicts.

    `duration` is a wall-clock budget (seconds). Each tool invocation is capped
    to the time remaining so a single `iw scan` cannot blow past the preset
    (e.g. PIXIE Rush scan=10).
    """
    iface_name = iface_mod.iface_name(ifname)
    budget = float(duration if duration is not None else 8)
    end = time.time() + budget

    # A prior attack can leave the external adapter in monitor mode (ifconfig
    # shows PROMISC + an UNSPEC hwaddr), where a normal scan returns nothing.
    # Force it back to managed + up unconditionally before scanning.
    try:
        iface_mod.disable_monitor(iface_name)
    except Exception:  # noqa: BLE001
        pass
    run([IP, "link", "set", iface_name, "down"], timeout=3)
    run([IW, "dev", iface_name, "set", "type", "managed"], timeout=3)
    run([IP, "link", "set", iface_name, "up"], timeout=3)
    # Settle eats into the budget; keep it short on rush scans.
    settle = min(0.4, max(0.0, end - time.time() - 1.0))
    if settle > 0:
        time.sleep(settle)
    _log("scanning on %s (iw=%s), budget=%.1fs" % (iface_name, IW, budget))

    nets = []

    def _remaining():
        return max(0.0, end - time.time())

    while _remaining() >= 1.0:
        if stop_flag and stop_flag.is_set():
            break
        # Cap iw so a hung USB stick leaves time for iwlist/nmcli fallbacks.
        tool_t = max(1.5, min(_remaining(), 6.0))

        # Try `iw` first (absolute path — systemd PATH lacks /usr/sbin).
        out = run([IW, "dev", iface_name, "scan"], timeout=tool_t)
        if out is not None and out.returncode != 0:
            _log("iw rc=%s err=%s" % (out.returncode, (out.stderr or "")[:80]))
        parsed = _parse_iw(out.stdout) if out and out.returncode == 0 else []

        if not parsed and _remaining() >= 1.5:
            out2 = run([IWLIST, iface_name, "scan"],
                       timeout=max(1.5, min(_remaining(), 6.0)))
            parsed = _parse_iwlist(out2.stdout) if out2 else []
            if parsed:
                _log("iwlist fallback: %d nets" % len(parsed))

        if not parsed and _remaining() >= 1.0:
            # nmcli fallback — pin to the external iface so it never scans wl0.
            # Often returns a cache quickly when a fresh iw scan timed out.
            out3 = run([NMCLI, "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,FREQ",
                        "dev", "wifi", "list", "ifname", iface_name, "--rescan",
                        "yes"],
                       timeout=max(1.0, min(_remaining(), 8.0)))
            if not (out3 and out3.returncode == 0 and out3.stdout.strip()):
                out3 = run([NMCLI, "-t", "-f", "SSID,BSSID,SIGNAL,CHAN,FREQ",
                            "dev", "wifi", "list", "ifname", iface_name],
                           timeout=max(1.0, min(_remaining(), 5.0)))
            if out3:
                parsed = _parse_nmcli(out3.stdout)
                if parsed:
                    _log("nmcli fallback: %d nets" % len(parsed))

        merged = {n["bssid"]: n for n in nets}
        for n in parsed:
            merged[n["bssid"]] = n
        nets = list(merged.values())

        if progress_cb:
            progress_cb(nets)          # pass the growing list (callers may use len)

        # A single `iw scan` already returns every nearby AP, so stop as soon
        # as a pass finds something instead of spinning the whole duration.
        if nets:
            break
        pause = min(0.4, _remaining())
        if pause < 0.2:
            break
        time.sleep(pause)     # nothing yet — brief pause, then retry

    _log("scan done: %d nets (elapsed=%.1fs)" % (
        len(nets), budget - _remaining()))

    # de-dup by bssid one final time
    seen = {}
    for n in nets:
        seen[n["bssid"]] = n
    return list(seen.values())


def _parse_nmcli(stdout):
    """Parse nmcli -t SSID:BSSID:SIGNAL:CHAN:FREQ (colon fields; BSSID escaped)."""
    nets = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        # Split on unescaped colons: BSSID bytes are written as AA\:BB\:...
        parts = re.split(r"(?<!\\):", line)
        if len(parts) < 3:
            continue
        ssid = parts[0].replace("\\:", ":")
        bssid = parts[1].replace("\\:", ":").upper()
        if not re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", bssid):
            continue
        try:
            # nmcli SIGNAL is 0-100; match prior convention of approximate -dBm.
            dbm = int(parts[2]) * -1
        except (ValueError, TypeError):
            dbm = 0
        channel = None
        if len(parts) > 3 and parts[3].isdigit():
            channel = parts[3]
        elif len(parts) > 4:
            channel = _freq_to_channel(parts[4].split()[0]
                                       if parts[4] else None)
        nets.append({"essid": ssid or None, "bssid": bssid,
                     "signal": dbm, "channel": channel, "enc": None})
    return nets


def _is_hex_byte(s):
    try:
        int(s.strip(), 16)
        return len(s.strip()) == 2
    except (ValueError, TypeError):
        return False


def sort_by_signal(nets):
    return sorted(nets, key=lambda n: n.get("signal") or 0, reverse=True)


def sort_wps_first(nets):
    """2.4 GHz + known-WPS first (pixie almost never works on 5 GHz)."""
    def _key(n):
        try:
            ch = int(n.get("channel") or 0)
        except (TypeError, ValueError):
            ch = 0
        band = 0 if 1 <= ch <= 14 else 1
        wps = 0 if n.get("wps") is True else 1
        return (band, wps, -(n.get("signal") or -999))
    return sorted(nets, key=_key)
