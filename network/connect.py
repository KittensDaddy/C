# -*- coding: utf-8 -*-
"""Connect to a known WiFi network.

Strategy: try the internal card first; if that fails, fall back to an external
(attack) card temporarily switched to managed mode.
"""
import subprocess
import time
import re

import config
from attack import interface as iface_mod


def run(cmd, timeout=25):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def _nmcli_connect(ifname, ssid, psk):
    """Return (ok, error_text). ok is None if nmcli is unavailable."""
    args = ["nmcli", "dev", "wifi", "connect", ssid]
    if psk:
        args += ["password", psk]
    if ifname:
        args += ["ifname", ifname]
    r = run(args, timeout=35)
    if r is None:
        return None, "nmcli unavailable"
    lines = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
    return (r.returncode == 0), (lines[-1] if lines else "")


def _delete_profiles(ssid):
    """Remove existing NM connection profiles for this SSID (incl. leftover
    netplan-* duplicates). Stale/duplicate profiles make `nmcli dev wifi connect`
    fail with an 802-11-wireless error; deleting them forces a clean new profile
    built from the password we pass."""
    r = run(["nmcli", "-t", "-f", "NAME", "con", "show"], timeout=8)
    if not r:
        return
    for name in r.stdout.splitlines():
        name = name.strip().replace("\\:", ":")
        if name and (name == ssid or name.endswith("-" + ssid)):
            run(["nmcli", "con", "delete", name], timeout=10)


def _nm_manage(ifname, managed=True):
    """Hand an interface (back) to NetworkManager — the external attack card is
    left unmanaged during attacks, so nmcli can't use it until re-managed."""
    run(["nmcli", "device", "set", ifname, "managed",
         "yes" if managed else "no"], timeout=8)


def _associated_ssid(ifname):
    """The SSID the interface is actually associated to, or None."""
    r = run(["iwgetid", ifname, "-r"], timeout=5)
    if r and r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    r = run(["/usr/sbin/iwconfig", ifname], timeout=5)
    if r:
        m = re.search(r'ESSID:"([^"]*)"', r.stdout)
        return m.group(1) if m else None
    return None


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
    """Connect to SSID. Association counts as success; internet is a bonus.

    Forces a clean disconnect first so re-selecting a network actually reconnects
    (nmcli otherwise no-ops if it thinks it's already connected). Returns
    (ok, interface_used, error)."""
    if progress_cb:
        progress_cb("Connecting %s..." % ssid)

    internal, external = iface_mod.classify(iface_mod.get_interfaces())
    candidates = []
    if internal:
        candidates.append(("internal", internal[0][0]))
    candidates += [("external", name) for name, _ in external]

    last_err = "no wifi interface"
    for label, name in candidates:
        if not name:
            continue
        if progress_cb:
            progress_cb("%s: %s" % (label, name))
        # External attack card: leave monitor mode + hand it back to NM.
        if label == "external":
            try:
                if iface_mod.is_in_monitor(name):
                    iface_mod.disable_monitor(name)
            except Exception:  # noqa: BLE001
                pass
            _nm_manage(name, True)

        # Force a clean disconnect + drop stale/duplicate profiles so a re-select
        # truly reconnects with the password we pass (avoids the 802-11 conflict).
        run(["nmcli", "device", "disconnect", name], timeout=15)
        _delete_profiles(ssid)
        time.sleep(0.5)

        ok, err = _nmcli_connect(name, ssid, psk)
        if ok is None:
            ok = _wpa_supplicant_connect(name, ssid, psk)
            err = "wpa_supplicant"
        # Success = actually associated to the target (don't require internet).
        if ok or _associated_ssid(name) == ssid:
            if progress_cb:
                progress_cb("connected" if has_internet()
                            else "connected (no internet)")
            return True, name, None
        last_err = err or "failed"
        if progress_cb:
            progress_cb("%s failed: %s" % (label, last_err[:16]))

    return False, None, last_err


def current_ssid():
    """Return currently associated SSID, or None."""
    r = run(["/usr/sbin/iwconfig"])
    if not r:
        return None
    m = re.search(r'ESSID:"(.*)"', r.stdout)
    return m.group(1) if m else None
