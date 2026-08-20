# -*- coding: utf-8 -*-
"""Connect to a known WiFi network.

Uses the internal card (brcmfmac / wl0) only — the external attack adapter is
never touched. nmcli's `wifi connect` needs a fresh scan list; after a forced
disconnect that list is often empty and nmcli returns "No network with SSID …",
so we rescan + retry, then fall back to a named connection profile.
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
    # Last line of nmcli is usually the useful bit; keep it short.
    line = e.splitlines()[-1] if e else "failed"
    line = re.sub(r"^Error:\s*", "", line, flags=re.I)
    return line[:18] or "failed"


def _nmcli_connect(ifname, ssid, psk):
    """Return (ok, error_text). ok is None if nmcli is unavailable."""
    args = ["nmcli", "dev", "wifi", "connect", ssid]
    if psk:
        args += ["password", psk]
    if ifname:
        args += ["ifname", ifname]
    r = run(args, timeout=45)
    if r is None:
        return None, "nmcli unavailable"
    lines = _nm_output(r).splitlines()
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
    """Hand an interface (back) to NetworkManager."""
    run(["nmcli", "device", "set", ifname, "managed",
         "yes" if managed else "no"], timeout=8)


def _ensure_ready(ifname):
    """Make sure the internal NIC is managed, not in monitor, and up."""
    try:
        if iface_mod.is_in_monitor(ifname):
            iface_mod.disable_monitor(ifname)
    except Exception:  # noqa: BLE001
        pass
    _nm_manage(ifname, True)
    run(["ip", "link", "set", ifname, "up"], timeout=5)
    # Kick NM to re-claim the device after monitor-mode churn.
    run(["nmcli", "device", "reapply", ifname], timeout=8)


def _wifi_rescan(ifname):
    """Force a scan so `wifi connect` can see the target SSID again."""
    run(["nmcli", "device", "wifi", "rescan", "ifname", ifname], timeout=15)
    # Scan results take a couple of seconds to land in NM's cache.
    time.sleep(2.5)


def _ssid_in_scan(ifname, ssid):
    r = run(["nmcli", "-t", "-f", "SSID", "device", "wifi", "list",
             "ifname", ifname], timeout=10)
    if not r or r.returncode != 0:
        return False
    for line in r.stdout.splitlines():
        name = line.strip().replace("\\:", ":")
        if name == ssid:
            return True
    return False


def _profile_connect(ifname, ssid, psk):
    """Create a wifi profile and bring it up — does not require a prior scan hit."""
    _delete_profiles(ssid)
    add = ["nmcli", "connection", "add",
           "type", "wifi", "con-name", ssid, "ifname", ifname,
           "ssid", ssid]
    if psk:
        add += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
    r = run(add, timeout=15)
    if r is None:
        return None, "nmcli unavailable"
    if r.returncode != 0:
        return False, _nm_output(r).splitlines()[-1] if _nm_output(r) else "add failed"
    r = run(["nmcli", "connection", "up", ssid, "ifname", ifname], timeout=45)
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


def connect(ssid, psk, progress_cb=None, stop_flag=None):
    """Connect to SSID using ONLY the internal wifi (wl0). Association counts as
    success; internet is a bonus. The external attack card is never touched.

    Forces a clean disconnect first so re-selecting a network actually reconnects
    (nmcli otherwise no-ops if it thinks it's already connected). Returns
    (ok, interface_used, error)."""
    if progress_cb:
        progress_cb("Connecting %s..." % ssid)

    name = _pick_internal()
    if not name:
        if progress_cb:
            progress_cb("no internal wifi")
        return False, None, "no internal wifi"

    if progress_cb:
        progress_cb("internal: %s" % name)

    _ensure_ready(name)

    # Force a clean disconnect + drop stale/duplicate profiles so a re-select
    # truly reconnects with the password we pass (avoids the 802-11 conflict).
    run(["nmcli", "device", "disconnect", name], timeout=15)
    _delete_profiles(ssid)
    time.sleep(0.5)

    last_err = "failed"
    # 1) Rescan + wifi connect (needs SSID in NM's scan cache).
    for attempt in range(3):
        if stop_flag and stop_flag.is_set():
            return False, name, "cancelled"
        if progress_cb:
            progress_cb("scan %d/3..." % (attempt + 1))
        _wifi_rescan(name)
        if not _ssid_in_scan(name, ssid):
            last_err = "ssid not seen"
            if progress_cb:
                progress_cb("ssid not in scan")
            continue
        if progress_cb:
            progress_cb("joining...")
        ok, err = _nmcli_connect(name, ssid, psk)
        if ok is None:
            # nmcli missing — one-shot wpa_supplicant path.
            ok = _wpa_supplicant_connect(name, ssid, psk)
            err = "wpa_supplicant"
        last_err = err or last_err
        if ok or _associated_ssid(name) == ssid:
            if progress_cb:
                progress_cb("connected" if has_internet()
                            else "connected (no internet)")
            return True, name, None

    # 2) Profile up — works even when the scan list is flaky.
    if progress_cb:
        progress_cb("profile join...")
    ok, err = _profile_connect(name, ssid, psk)
    last_err = err or last_err
    if ok is None:
        ok = _wpa_supplicant_connect(name, ssid, psk)
        last_err = "wpa_supplicant"
    if ok or _associated_ssid(name) == ssid:
        if progress_cb:
            progress_cb("connected" if has_internet()
                        else "connected (no internet)")
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
