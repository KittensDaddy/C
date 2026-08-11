# -*- coding: utf-8 -*-
"""Connect to a known WiFi network.

Strategy: try the internal card first; if that fails, fall back to an external
(attack) card temporarily switched to managed mode.
"""
import subprocess
import time
import re

import config
from wifite import interface as iface_mod


def run(cmd, timeout=25):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def _nmcli_connect(ifname, ssid, psk):
    args = ["nmcli", "dev", "wifi", "connect", ssid]
    if psk:
        args += ["password", psk]
    if ifname:
        args += ["ifname", ifname]
    r = run(args)
    if r is None:
        return None
    return r.returncode == 0


def _wpa_supplicant_connect(ifname, ssid, psk):
    """Fallback when nmcli is unavailable."""
    try:
        conf = "/tmp/wpa_%s.conf" % ifname
        with open(conf, "w") as f:
            f.write('network={\n ssid="%s"\n psk="%s"\n key_mgmt=WPA-PSK\n}\n'
                    % (ssid, psk))
        run(["ip", "link", "set", ifname, "down"])
        run(["iw", ifname, "set", "type", "managed"])
        run(["ip", "link", "set", ifname, "up"])
        run(["wpa_supplicant", "-B", "-i", ifname, "-c", conf])
        time.sleep(5)
        # try dhcpcd (Pi OS default), else dhclient, else nmcli dhcp
        if not _obtain_lease(ifname):
            run(["nmcli", "device", "connect", ifname])
        return has_internet()
    except Exception:  # noqa: BLE001
        return False


def _obtain_lease(ifname):
    for tool in ("dhcpcd", "dhclient"):
        if run([tool, ifname], timeout=20):
            return True
    return False


def has_internet(timeout=5):
    """Best-effort internet check via ping to 8.8.8.8 / 100.124.251.39."""
    for host in ("8.8.8.8", config.UPLOAD_SERVER):
        r = run(["ping", "-c", "1", "-W", "2", host], timeout=timeout + 2)
        if r and r.returncode == 0:
            return True
    return False


def connect(ssid, psk, progress_cb=None, stop_flag=None):
    """Connect to SSID. Returns (ok, interface_used, error)."""
    if progress_cb:
        progress_cb("Connecting %s..." % ssid)

    # 1) internal wifi
    internal, external = iface_mod.classify(iface_mod.get_interfaces())
    internal_name = internal[0][0] if internal else None
    for label, name in (("internal", internal_name),):
        if not name:
            continue
        if progress_cb:
            progress_cb("%s: %s" % (label, name))
        ok = _nmcli_connect(name, ssid, psk)
        if ok is None:
            ok = _wpa_supplicant_connect(name, ssid, psk)
        if ok and has_internet():
            return True, name, None
        # fall through to external

    # 2) external cards (managed mode temporarily)
    for name, _ in external:
        if progress_cb:
            progress_cb("external: %s" % name)
        # ensure it's not in monitor mode
        try:
            if iface_mod.is_in_monitor(name):
                iface_mod.disable_monitor(name)
        except Exception:  # noqa: BLE001
            pass
        ok = _nmcli_connect(name, ssid, psk)
        if ok is None:
            ok = _wpa_supplicant_connect(name, ssid, psk)
        if ok and has_internet():
            return True, name, None

    return False, None, "Could not connect (checked internal + external)"


def current_ssid():
    """Return currently associated SSID, or None."""
    r = run(["/usr/sbin/iwconfig"])
    if not r:
        return None
    m = re.search(r'ESSID:"(.*)"', r.stdout)
    return m.group(1) if m else None
