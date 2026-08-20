# -*- coding: utf-8 -*-
"""Connect to a known WiFi network.

Uses the internal card (brcmfmac / wl0) only — the external attack adapter is
never touched. Prefer joining by BSSID when cracked.json has one; never block
on an imperfect nmcli scan-list match (that produced false "ssid not in scan").
"""
import re
import subprocess
import time

import config
from attack import interface as iface_mod


def run(cmd, timeout=25):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


def _nm_output(r):
    if not r:
        return ""
    return ((r.stderr or "") + "\n" + (r.stdout or "")).strip()


def _short_err(err):
    """Map verbose nmcli text to something that fits the 128px LCD."""
    e = (err or "").strip()
    low = e.lower()
    if "no network with ssid" in low or "network not found" in low:
        return "ssid not seen"
    if "secrets were required" in low or "no secrets" in low:
        return "bad password"
    if "wrong password" in low or "802-11-wireless-security" in low:
        return "bad password"
    if "no internal" in low:
        return "no internal wifi"
    if "timeout" in low:
        return "timeout"
    line = e.splitlines()[-1] if e else "failed"
    line = re.sub(r"^Error:\s*", "", line, flags=re.I)
    return line[:18] or "failed"


def _norm_bssid(bssid):
    if not bssid:
        return None
    s = bssid.strip().upper().replace("-", ":")
    if re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", s):
        return s
    return None


def _nmcli_connect(ifname, ssid, psk, bssid=None):
    """Return (ok, error_text). ok is None if nmcli is unavailable."""
    args = ["nmcli", "dev", "wifi", "connect", ssid]
    if psk:
        args += ["password", psk]
    if ifname:
        args += ["ifname", ifname]
    if bssid:
        args += ["bssid", bssid]
    r = run(args, timeout=45)
    if r is None:
        return None, "nmcli unavailable"
    lines = _nm_output(r).splitlines()
    return (r.returncode == 0), (lines[-1] if lines else "")


def _delete_profiles(ssid):
    """Remove existing NM connection profiles for this SSID (incl. leftover
    netplan-* duplicates)."""
    r = run(["nmcli", "-t", "-f", "NAME", "con", "show"], timeout=8)
    if not r:
        return
    for name in r.stdout.splitlines():
        name = name.strip().replace("\\:", ":")
        if name and (name == ssid or name.endswith("-" + ssid)):
            run(["nmcli", "con", "delete", name], timeout=10)


def _nm_manage(ifname, managed=True):
    run(["nmcli", "device", "set", ifname, "managed",
         "yes" if managed else "no"], timeout=8)


def _ensure_ready(ifname):
    """Make sure the internal NIC is managed, not in monitor, and up."""
    try:
        if iface_mod.is_in_monitor(ifname):
            iface_mod.disable_monitor(ifname)
    except Exception:  # noqa: BLE001
        pass
    run(["nmcli", "radio", "wifi", "on"], timeout=5)
    _nm_manage(ifname, True)
    run(["ip", "link", "set", ifname, "up"], timeout=5)
    run(["nmcli", "device", "reapply", ifname], timeout=8)


def _wifi_rescan(ifname, wait=4.0):
    """Force a scan; wait for results to land in NM's cache."""
    run(["nmcli", "device", "wifi", "rescan", "ifname", ifname], timeout=20)
    # Also poke the kernel scan — helps brcmfmac after a disconnect.
    run(["iw", "dev", ifname, "scan", "trigger"], timeout=8)
    time.sleep(wait)


def _profile_connect(ifname, ssid, psk, bssid=None):
    """Create a wifi profile and bring it up — no scan-list required."""
    _delete_profiles(ssid)
    add = ["nmcli", "connection", "add",
           "type", "wifi", "con-name", ssid, "ifname", ifname,
           "ssid", ssid]
    if bssid:
        add += ["wifi.bssid", bssid]
    if psk:
        add += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
    r = run(add, timeout=15)
    if r is None:
        return None, "nmcli unavailable"
    if r.returncode != 0:
        out = _nm_output(r)
        return False, out.splitlines()[-1] if out else "add failed"
    r = run(["nmcli", "--wait", "30", "connection", "up", ssid,
             "ifname", ifname], timeout=45)
    if r is None:
        return None, "nmcli unavailable"
    lines = _nm_output(r).splitlines()
    return (r.returncode == 0), (lines[-1] if lines else "")


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


def _wpa_supplicant_connect(ifname, ssid, psk, bssid=None):
    """Fallback when nmcli is unavailable."""
    try:
        conf = "/tmp/wpa_%s.conf" % ifname
        with open(conf, "w") as f:
            f.write("network={\n")
            f.write(' ssid="%s"\n' % ssid)
            if bssid:
                f.write(" bssid=%s\n" % bssid.lower())
            f.write(' psk="%s"\n key_mgmt=WPA-PSK\n}\n' % psk)
        run(["ip", "link", "set", ifname, "down"])
        run(["iw", ifname, "set", "type", "managed"])
        run(["ip", "link", "set", ifname, "up"])
        run(["wpa_supplicant", "-B", "-i", ifname, "-c", conf])
        time.sleep(5)
        if not _obtain_lease(ifname):
            run(["nmcli", "device", "connect", ifname])
        return _associated_ssid(ifname) == ssid or has_internet()
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


def _pick_internal():
    """Prefer brcmfmac; fall back to config.INTERNAL_NAME if classify misses."""
    ifaces = iface_mod.get_interfaces()
    internal, _external = iface_mod.classify(ifaces)
    if internal:
        return internal[0][0]
    want = getattr(config, "INTERNAL_NAME", None) or "wl0"
    for name, _drv in ifaces:
        if name == want:
            return name
    return None


def _joined(ifname, ssid):
    got = _associated_ssid(ifname)
    return bool(got and got == ssid)


def connect(ssid, psk, progress_cb=None, stop_flag=None, bssid=None):
    """Connect to SSID using ONLY the internal wifi (wl0). Association counts as
    success; internet is a bonus. The external attack card is never touched.

    `bssid` (optional) pins the AP — more reliable than SSID-only when the
    nmcli scan cache is empty/stale after disconnect.

    Returns (ok, interface_used, error)."""
    ssid = (ssid or "").strip()
    bssid = _norm_bssid(bssid)
    if progress_cb:
        progress_cb("Connecting %s..." % (ssid[:16] or "?"))

    name = _pick_internal()
    if not name:
        if progress_cb:
            progress_cb("no internal wifi")
        return False, None, "no internal wifi"

    if progress_cb:
        progress_cb("internal: %s" % name)
        if bssid:
            progress_cb("bssid %s" % bssid[-8:])

    _ensure_ready(name)

    # Warm the scan cache WHILE still associated (home), then cut over.
    if progress_cb:
        progress_cb("scanning...")
    _wifi_rescan(name, wait=3.0)

    run(["nmcli", "device", "disconnect", name], timeout=15)
    _delete_profiles(ssid)
    time.sleep(0.3)

    last_err = "failed"
    # Always try to join — do not gate on scan-list string match.
    for attempt in range(3):
        if stop_flag and stop_flag.is_set():
            return False, name, "cancelled"
        if attempt:
            if progress_cb:
                progress_cb("retry %d/3..." % (attempt + 1))
            _wifi_rescan(name, wait=4.0)
        if progress_cb:
            progress_cb("joining...")
        ok, err = _nmcli_connect(name, ssid, psk, bssid=bssid)
        if ok is None:
            ok = _wpa_supplicant_connect(name, ssid, psk, bssid=bssid)
            err = "wpa_supplicant"
        last_err = err or last_err
        if ok or _joined(name, ssid):
            if progress_cb:
                progress_cb("connected" if has_internet()
                            else "connected (no net)")
            return True, name, None

    if progress_cb:
        progress_cb("profile join...")
    ok, err = _profile_connect(name, ssid, psk, bssid=bssid)
    last_err = err or last_err
    if ok is None:
        ok = _wpa_supplicant_connect(name, ssid, psk, bssid=bssid)
        last_err = "wpa_supplicant"
    if ok or _joined(name, ssid):
        if progress_cb:
            progress_cb("connected" if has_internet()
                        else "connected (no net)")
        return True, name, None

    short = _short_err(last_err)
    if progress_cb:
        progress_cb("failed: %s" % short)
    return False, name, short


def restore_network():
    """Restart NetworkManager so the internal wl0 reconnects to its saved home
    network — restores SSH after an attack's monitor-mode churn disrupted it."""
    for cmd in (["systemctl", "restart", "NetworkManager"],
                ["sudo", "systemctl", "restart", "NetworkManager"]):
        r = run(cmd, timeout=20)
        if r is not None and r.returncode == 0:
            return True
    return False


def current_ssid():
    """Return currently associated SSID, or None."""
    r = run(["/usr/sbin/iwconfig"])
    if not r:
        return None
    m = re.search(r'ESSID:"(.*)"', r.stdout)
    return m.group(1) if m else None
