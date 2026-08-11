# -*- coding: utf-8 -*-
"""Wireless interface detection and monitor-mode control with fallbacks.

Attacks use the EXTERNAL USB card (any interface whose driver is not the
internal brcmfmac). The internal card is left alone unless explicitly needed
by the network/connect module.
"""
import subprocess
import time

import config


def run(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return None
    except Exception:  # noqa: BLE001
        return None


def _iwconfig_scan():
    """Parse `iwconfig` for wireless interface names + driver via ethtool."""
    out = run(["/usr/sbin/iwconfig"])
    if out is None:
        return []
    result = []
    for line in out.stdout.splitlines():
        # An interface header line contains "IEEE 802.11". Mode:Frequency and
        # other detail lines are indented and must not be treated as names.
        if "IEEE 802.11" in line:
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            if not name or name == "IEEE":
                continue
            driver = _driver_of(name)
            result.append((name, driver))
    # de-dup, preserving order
    seen, uniq = set(), []
    for item in result:
        if item[0] not in seen:
            seen.add(item[0])
            uniq.append(item)
    return uniq


def _driver_of(ifname):
    out = run(["/usr/sbin/ethtool", "-i", ifname])
    if out is None:
        return "unknown"
    for line in out.stdout.splitlines():
        if line.lower().startswith("driver"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return "unknown"


def _ls_interfaces():
    """Fallback: list /sys/class/net wireless-capable interfaces."""
    import os
    names = []
    try:
        base = "/sys/class/net"
        for name in os.listdir(base):
            wireless = os.path.join(base, name, "wireless")
            if os.path.exists(wireless):
                names.append((name, _driver_of(name)))
    except Exception:  # noqa: BLE001
        pass
    return names


def get_interfaces():
    """Return list of (name, driver) wireless interfaces. Smart fallback."""
    ifaces = _iwconfig_scan()
    if not ifaces:
        ifaces = _ls_interfaces()
    return ifaces


def classify(ifaces):
    """Split interfaces into (internal, external_list)."""
    internal = []
    external = []
    for name, driver in ifaces:
        if driver == config.INTERNAL_DRIVER:
            internal.append((name, driver))
        else:
            external.append((name, driver))
    return internal, external


def pick_external(ifaces):
    """Return the preferred external interface or None."""
    internal, external = classify(ifaces)
    if external:
        return external[0]
    return None


def is_in_monitor(ifname):
    out = run(["/usr/sbin/iwconfig", ifname])
    if out is None:
        return False
    return "Mode:Monitor" in out.stdout


def enable_monitor(ifname):
    """Put interface in monitor mode with multiple fallbacks."""
    name = ifname[0] if isinstance(ifname, tuple) else ifname

    # 1) airmon-ng
    if run(["/usr/sbin/airmon-ng", "start", name]) is not None:
        time.sleep(2)
        if is_in_monitor(name):
            return True
        # airmon may rename to <name>mon
        mon = name + "mon"
        if is_in_monitor(mon):
            return True

    # 2) iw directly
    if run(["ip", "link", "set", name, "down"]) is not None:
        run(["iw", name, "set", "type", "monitor"])
        run(["ip", "link", "set", name, "up"])
        time.sleep(1)
        if is_in_monitor(name):
            return True
    return False


def disable_monitor(ifname):
    """Return interface to managed mode with multiple fallbacks."""
    name = ifname[0] if isinstance(ifname, tuple) else ifname
    if run(["ip", "link", "set", name, "down"]) is not None:
        run(["iw", name, "set", "type", "managed"])
        run(["ip", "link", "set", name, "up"])
        time.sleep(1)
    if run(["/usr/sbin/airmon-ng", "stop", name]) is not None:
        time.sleep(1)


def iface_name(iface):
    return iface[0] if isinstance(iface, tuple) else iface
