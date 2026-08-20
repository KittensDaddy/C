# -*- coding: utf-8 -*-
"""Connect to a known WiFi network.

Uses the internal card (brcmfmac / wl0) only — the external attack adapter is
never touched.

Join order (activation failures are common when a stale/wrong BSSID is pinned):
  1. nmcli wifi connect by SSID only
  2. same with BSSID (if cracked.json has one)
  3. named profile up (no BSSID lock, permanent MAC)
  4. profile up pinned to BSSID
  5. wpa_supplicant with NM unmanaged
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


def _log(msg):
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write("[connect] %s\n" % msg)
    except Exception:  # noqa: BLE001
        pass


def _nm_output(r):
    if not r:
        return ""
    return ((r.stderr or "") + "\n" + (r.stdout or "")).strip()


def _short_err(err):
    """Map verbose nmcli text to something that fits the 128px LCD."""
    e = (err or "").strip()
    low = e.lower()
    if "no network with ssid" in low or "network not found" in low \
            or "could not be found" in low:
        return "ap not found"
    if "secrets were required" in low or "no secrets" in low:
        return "bad password"
    if "wrong password" in low or "802-11-wireless-security" in low:
        return "bad password"
    if "no suitable device" in low:
        return "no device"
    if "device not ready" in low:
        return "dev not ready"
    if "association took too long" in low or "assoc" in low and "fail" in low:
        return "assoc timeout"
    if "activation failed" in low or "connection activation" in low:
        return "join failed"
    if "no internal" in low:
        return "no internal wifi"
    if "timeout" in low:
        return "timeout"
    line = e.splitlines()[-1] if e else "failed"
    line = re.sub(r"^Error:\s*", "", line, flags=re.I)
    line = re.sub(r"^Connection activation failed:\s*", "", line, flags=re.I)
    return (line[:18] or "failed").strip()


def _norm_bssid(bssid):
    if not bssid:
        return None
    raw = re.sub(r"[^0-9A-Fa-f]", "", str(bssid))
    if len(raw) != 12:
        return None
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2)).upper()


def _nmcli_connect(ifname, ssid, psk, bssid=None):
    """Return (ok, error_text). ok is None if nmcli is unavailable."""
    args = ["nmcli", "--wait", "45", "dev", "wifi", "connect", ssid]
    if psk:
        args += ["password", psk]
    if ifname:
        args += ["ifname", ifname]
    if bssid:
        args += ["bssid", bssid]
    _log("wifi connect: %s" % " ".join(
        a if a != psk else "***" for a in args))
    r = run(args, timeout=60)
    if r is None:
        return None, "nmcli unavailable"
    out = _nm_output(r)
    _log("wifi connect rc=%s: %s" % (r.returncode, out[:200]))
    lines = out.splitlines()
    return (r.returncode == 0), (lines[-1] if lines else "")


def _delete_profiles(ssid):
    """Remove existing NM connection profiles for this SSID (incl. leftover
    netplan-* duplicates)."""
    r = run(["nmcli", "-t", "-f", "NAME,UUID", "con", "show"], timeout=8)
    if not r:
        return
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        # NAME may contain escaped colons; UUID is last field.
        uuid = parts[-1].strip()
        name = ":".join(parts[:-1]).replace("\\:", ":").strip()
        if name and (name == ssid or name.endswith("-" + ssid) or
                     name.startswith(ssid + "-")):
            run(["nmcli", "con", "delete", uuid or name], timeout=10)
            _log("deleted profile %s" % name)


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
    run(["rfkill", "unblock", "wifi"], timeout=5)
    run(["nmcli", "radio", "wifi", "on"], timeout=5)
    _nm_manage(ifname, True)
    run(["ip", "link", "set", ifname, "up"], timeout=5)
    # Wait until NM sees the device as usable.
    run(["nmcli", "device", "wait", ifname], timeout=12)
    run(["nmcli", "device", "reapply", ifname], timeout=8)


def _wifi_rescan(ifname, wait=4.0):
    """Force a scan; wait for results to land in NM's cache."""
    run(["nmcli", "device", "wifi", "rescan", "ifname", ifname], timeout=20)
    run(["iw", "dev", ifname, "scan", "trigger"], timeout=8)
    time.sleep(wait)


def _profile_connect(ifname, ssid, psk, bssid=None):
    """Create a wifi profile and bring it up."""
    _delete_profiles(ssid)
    # Unique con-name avoids clashing with leftover UUID-named profiles.
    con = "wifibox-%s" % re.sub(r"[^A-Za-z0-9_-]", "_", ssid)[:24]
    add = [
        "nmcli", "connection", "add",
        "type", "wifi",
        "con-name", con,
        "ifname", ifname,
        "ssid", ssid,
        "connection.autoconnect", "yes",
        "wifi.mode", "infrastructure",
        "wifi.mac-address-randomization", "never",
        "wifi.cloned-mac-address", "permanent",
        "ipv4.method", "auto",
        "ipv6.method", "auto",
    ]
    if bssid:
        add += ["wifi.bssid", bssid]
    if psk:
        add += [
            "wifi-sec.key-mgmt", "wpa-psk",
            "wifi-sec.psk", psk,
            "wifi-sec.auth-alg", "open",
        ]
    _log("profile add: %s bssid=%s" % (con, bssid or "-"))
    r = run(add, timeout=15)
    if r is None:
        return None, "nmcli unavailable"
    if r.returncode != 0:
        out = _nm_output(r)
        _log("profile add fail: %s" % out[:200])
        return False, out.splitlines()[-1] if out else "add failed"

    up = ["nmcli", "--wait", "45", "connection", "up", con, "ifname", ifname]
    if bssid:
        up += ["ap", bssid]
    r = run(up, timeout=60)
    if r is None:
        return None, "nmcli unavailable"
    out = _nm_output(r)
    _log("profile up rc=%s: %s" % (r.returncode, out[:200]))
    lines = out.splitlines()
    return (r.returncode == 0), (lines[-1] if lines else "")


def _associated_ssid(ifname):
    """The SSID the interface is actually associated to, or None."""
    r = run(["iwgetid", ifname, "-r"], timeout=5)
    if r and r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    r = run(["iw", "dev", ifname, "link"], timeout=5)
    if r and r.returncode == 0:
        m = re.search(r"SSID:\s*(.+)$", r.stdout, re.M)
        if m:
            return m.group(1).strip()
    r = run(["/usr/sbin/iwconfig", ifname], timeout=5)
    if r:
        m = re.search(r'ESSID:"([^"]*)"', r.stdout)
        return m.group(1) if m else None
    return None


def _wpa_supplicant_connect(ifname, ssid, psk, bssid=None):
    """Last-resort join with NM hands off the interface."""
    try:
        _nm_manage(ifname, False)
        time.sleep(0.5)
        # Kill any stray wpa_supplicant we previously spawned on this iface.
        run(["pkill", "-f", "wpa_supplicant.*-i %s" % ifname], timeout=5)
        time.sleep(0.3)
        conf = "/tmp/wpa_%s.conf" % ifname
        with open(conf, "w") as f:
            f.write("ctrl_interface=DIR=/var/run/wpa_supplicant\n")
            f.write("network={\n")
            f.write(' ssid="%s"\n' % ssid.replace("\\", "\\\\").replace('"', '\\"'))
            if bssid:
                f.write(" bssid=%s\n" % bssid.lower())
            f.write(' psk="%s"\n' % psk.replace("\\", "\\\\").replace('"', '\\"'))
            f.write(" key_mgmt=WPA-PSK\n}\n")
        run(["ip", "link", "set", ifname, "down"])
        run(["iw", "dev", ifname, "set", "type", "managed"])
        run(["ip", "link", "set", ifname, "up"])
        run(["wpa_supplicant", "-B", "-i", ifname, "-c", conf, "-D", "nl80211"])
        deadline = time.time() + 20
        while time.time() < deadline:
            if _associated_ssid(ifname) == ssid:
                break
            time.sleep(1)
        ok = _associated_ssid(ifname) == ssid
        if ok:
            _obtain_lease(ifname)
        _log("wpa_supplicant ok=%s ssid=%s" % (ok, _associated_ssid(ifname)))
        return ok
    except Exception as e:  # noqa: BLE001
        _log("wpa_supplicant err: %s" % e)
        return False
    finally:
        # Hand back to NM so later Connect / home wifi still works.
        _nm_manage(ifname, True)


def _obtain_lease(ifname):
    for tool in ("dhcpcd", "dhclient"):
        r = run([tool, ifname], timeout=20)
        if r is not None:
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

    _ensure_ready(name)

    if progress_cb:
        progress_cb("scanning...")
    _wifi_rescan(name, wait=3.0)

    run(["nmcli", "device", "disconnect", name], timeout=15)
    _delete_profiles(ssid)
    time.sleep(0.5)
    _wifi_rescan(name, wait=3.5)

    last_err = "failed"
    # Attempts: SSID-only first (BSSID pin often causes "activation failed"
    # when the cracked MAC is 5GHz-only / stale / from another radio).
    attempts = [
        ("join ssid...", False),
        ("retry ssid...", False),
    ]
    if bssid:
        attempts += [
            ("join bssid...", True),
            ("retry bssid...", True),
        ]

    for label, use_bssid in attempts:
        if stop_flag and stop_flag.is_set():
            return False, name, "cancelled"
        if progress_cb:
            progress_cb(label)
        pin = bssid if use_bssid else None
        ok, err = _nmcli_connect(name, ssid, psk, bssid=pin)
        if ok is None:
            # nmcli missing entirely
            break
        last_err = err or last_err
        if ok or _joined(name, ssid):
            if progress_cb:
                progress_cb("connected" if has_internet()
                            else "connected (no net)")
            return True, name, None
        _wifi_rescan(name, wait=3.0)

    # Named profile (no BSSID), then with BSSID.
    for label, pin in (("profile...", None),
                       ("profile+mac...", bssid if bssid else None)):
        if pin is None and label.startswith("profile+"):
            continue
        if stop_flag and stop_flag.is_set():
            return False, name, "cancelled"
        if progress_cb:
            progress_cb(label)
        _wifi_rescan(name, wait=2.5)
        ok, err = _profile_connect(name, ssid, psk, bssid=pin)
        if ok is None:
            last_err = err or last_err
            break
        last_err = err or last_err
        if ok or _joined(name, ssid):
            if progress_cb:
                progress_cb("connected" if has_internet()
                            else "connected (no net)")
            return True, name, None

    if progress_cb:
        progress_cb("wpa fallback...")
    if _wpa_supplicant_connect(name, ssid, psk, bssid=None):
        if progress_cb:
            progress_cb("connected" if has_internet()
                        else "connected (no net)")
        return True, name, None

    short = _short_err(last_err)
    if progress_cb:
        progress_cb("failed: %s" % short)
    _log("FAILED %s -> %s (%s)" % (ssid, short, last_err))
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
