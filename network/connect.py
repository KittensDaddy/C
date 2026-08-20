# -*- coding: utf-8 -*-
"""Connect to a known WiFi network via the internal card only.

On the Pi, nmcli `wifi connect` / profile-up routinely fail after attacks
(scan cache empty, wlan0 strictly unmanaged). Direct wpa_supplicant is what
actually associates — so that is the primary path. One short nmcli attempt
runs first; on any miss we go straight to wpa (no long retry ladder).

Disconnect home first so the switch is real; on failure, restore the prior
NM profile so SSH on the LAN survives.
"""
import os
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
    if "device not ready" in low or "unmanaged" in low:
        return "dev not ready"
    if "association took too long" in low:
        return "assoc timeout"
    if "activation failed" in low or "connection activation" in low:
        return "join failed"
    if "no internal" in low:
        return "no internal wifi"
    if "timeout" in low:
        return "timeout"
    if "wpa" in low and "fail" in low:
        return "wpa failed"
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


def _escape_wpa(s):
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def _nm_manage(ifname, managed=True):
    run(["nmcli", "device", "set", ifname, "managed",
         "yes" if managed else "no"], timeout=8)
    if managed:
        time.sleep(0.2)
        run(["nmcli", "device", "set", ifname, "managed", "yes"], timeout=8)


def _device_unmanaged(ifname):
    r = run(["nmcli", "-t", "-f", "GENERAL.STATE", "device", "show", ifname],
            timeout=5)
    return bool(r and "unmanaged" in (r.stdout or "").lower())


def _ensure_ready(ifname):
    try:
        if iface_mod.is_in_monitor(ifname):
            iface_mod.disable_monitor(ifname)
    except Exception:  # noqa: BLE001
        pass
    run(["rfkill", "unblock", "wifi"], timeout=5)
    run(["nmcli", "radio", "wifi", "on"], timeout=5)
    for _ in range(3):
        _nm_manage(ifname, True)
        run(["ip", "link", "set", ifname, "up"], timeout=5)
        if not _device_unmanaged(ifname):
            break
        time.sleep(0.4)


def _delete_target_profiles(ssid):
    """Delete only profiles for the cracked target — never home."""
    r = run(["nmcli", "-t", "-f", "NAME,UUID", "con", "show"], timeout=8)
    if not r:
        return
    tag = _con_name(ssid)
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        uuid = parts[-1].strip()
        name = ":".join(parts[:-1]).replace("\\:", ":").strip()
        if not name:
            continue
        if name == ssid or name == tag or name.endswith("-" + ssid) \
                or name.startswith(ssid + "-"):
            run(["nmcli", "con", "delete", uuid or name], timeout=10)
            _log("deleted profile %s" % name)


def _con_name(ssid):
    return "wifibox-%s" % re.sub(r"[^A-Za-z0-9_-]", "_", ssid)[:24]


def _install_nm_profile(ifname, ssid, psk, bssid=None):
    """Write an NM profile for a link we already hold (or will bring up)."""
    con = _con_name(ssid)
    _delete_target_profiles(ssid)
    add = [
        "nmcli", "connection", "add",
        "type", "wifi", "con-name", con, "ifname", ifname, "ssid", ssid,
        "connection.autoconnect", "no",
        "wifi.mode", "infrastructure",
        "wifi.mac-address-randomization", "never",
        "wifi.cloned-mac-address", "permanent",
        "ipv4.method", "auto", "ipv6.method", "ignore",
    ]
    if bssid:
        add += ["wifi.bssid", bssid]
    if psk:
        add += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
    r = run(add, timeout=15)
    if not r or r.returncode != 0:
        _log("install profile fail: %s" % _nm_output(r)[:160])
        return None
    return con


def _nmcli_quick(ifname, ssid, psk):
    """Single short nmcli attempt — often fails on Pi after disconnect."""
    args = ["nmcli", "--wait", "20", "dev", "wifi", "connect", ssid,
            "password", psk, "ifname", ifname]
    _log("nmcli quick: %s" % ssid)
    r = run(args, timeout=30)
    if r is None:
        return None, "nmcli unavailable"
    out = _nm_output(r)
    _log("nmcli quick rc=%s: %s" % (r.returncode, out[:160]))
    return (r.returncode == 0), (out.splitlines()[-1] if out else "")


def _associated_ssid(ifname):
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


def _joined(ifname, ssid):
    got = _associated_ssid(ifname)
    return bool(got and got == ssid)


def _obtain_lease(ifname):
    for tool in ("dhclient", "dhcpcd"):
        # Renew cleanly.
        run([tool, "-r", ifname], timeout=8)
        r = run([tool, ifname], timeout=25)
        if r is not None and r.returncode == 0:
            return True
    # NM might still hand out DHCP if we re-manage later.
    return False


def _has_ipv4(ifname):
    r = run(["ip", "-4", "-o", "addr", "show", "dev", ifname], timeout=5)
    return bool(r and r.returncode == 0 and "inet " in (r.stdout or ""))


def _wpa_connect(ifname, ssid, psk, bssid=None, progress_cb=None):
    """Primary join: unmanaged → wpa_supplicant → DHCP → NM profile handoff."""
    if progress_cb:
        progress_cb("wpa join...")
    conf = "/tmp/wpa_wifibox_%s.conf" % ifname
    ctrl = "/var/run/wpa_supplicant"
    try:
        os.makedirs(ctrl, exist_ok=True)
    except OSError:
        pass

    # Build conf — prefer wpa_passphrase for correct PSK hashing.
    network_block = None
    wp = run(["wpa_passphrase", ssid, psk], timeout=5)
    if wp and wp.returncode == 0 and "psk=" in (wp.stdout or ""):
        network_block = wp.stdout.strip()
        if bssid:
            network_block = network_block.replace(
                "network={", "network={\n\tbssid=%s" % bssid.lower(), 1)
    if not network_block:
        lines = ["network={", '\tssid="%s"' % _escape_wpa(ssid)]
        if bssid:
            lines.append("\tbssid=%s" % bssid.lower())
        lines += ['\tpsk="%s"' % _escape_wpa(psk), "\tkey_mgmt=WPA-PSK", "}"]
        network_block = "\n".join(lines)

    with open(conf, "w") as f:
        f.write("ctrl_interface=%s\nupdate_config=0\nap_scan=1\n\n" % ctrl)
        f.write(network_block)
        f.write("\n")

    # Hands off from NetworkManager.
    run(["nmcli", "device", "disconnect", ifname], timeout=10)
    _nm_manage(ifname, False)
    time.sleep(0.4)
    run(["pkill", "-f", "wpa_supplicant.*-i %s" % ifname], timeout=5)
    time.sleep(0.3)

    run(["ip", "link", "set", ifname, "down"], timeout=5)
    run(["iw", "dev", ifname, "set", "type", "managed"], timeout=5)
    run(["ip", "addr", "flush", "dev", ifname], timeout=5)
    run(["ip", "link", "set", ifname, "up"], timeout=5)

    r = run(["wpa_supplicant", "-B", "-i", ifname, "-c", conf, "-D", "nl80211,wext"],
            timeout=10)
    if not r or r.returncode != 0:
        _log("wpa spawn fail: %s" % _nm_output(r)[:160])
        _nm_manage(ifname, True)
        return False, "wpa spawn fail"

    if progress_cb:
        progress_cb("associating...")
    deadline = time.time() + 25
    while time.time() < deadline:
        if _joined(ifname, ssid):
            break
        time.sleep(0.8)
    if not _joined(ifname, ssid):
        _log("wpa associate fail, ssid now=%s" % _associated_ssid(ifname))
        run(["pkill", "-f", "wpa_supplicant.*-i %s" % ifname], timeout=5)
        _nm_manage(ifname, True)
        return False, "wpa assoc fail"

    if progress_cb:
        progress_cb("dhcp...")
    _obtain_lease(ifname)
    # Give DHCP a moment even if the client returned early.
    for _ in range(8):
        if _has_ipv4(ifname):
            break
        time.sleep(0.5)

    # Hand off to NM so the rest of the app (tailscale/upload) sees a normal
    # managed device — but keep the link: install profile, then re-manage + up.
    con = _install_nm_profile(ifname, ssid, psk, bssid=None)
    run(["pkill", "-f", "wpa_supplicant.*-i %s" % ifname], timeout=5)
    time.sleep(0.3)
    _nm_manage(ifname, True)
    if con:
        up = run(["nmcli", "--wait", "25", "connection", "up", con,
                  "ifname", ifname], timeout=35)
        _log("nm handoff up rc=%s: %s" % (
            up.returncode if up else None, _nm_output(up)[:120]))
        if up and up.returncode == 0 and _joined(ifname, ssid):
            _log("wpa→nm handoff ok")
            return True, None
        # Handoff dropped the link — fall back to staying on raw wpa.
        _log("nm handoff lost link; re-wpa")
        _nm_manage(ifname, False)
        run(["wpa_supplicant", "-B", "-i", ifname, "-c", conf,
             "-D", "nl80211,wext"], timeout=10)
        deadline = time.time() + 15
        while time.time() < deadline and not _joined(ifname, ssid):
            time.sleep(0.5)
        _obtain_lease(ifname)

    ok = _joined(ifname, ssid)
    _log("wpa final ok=%s ip=%s ssid=%s" % (
        ok, _has_ipv4(ifname), _associated_ssid(ifname)))
    return ok, (None if ok else "wpa failed")


def has_internet(timeout=5):
    for host in ("8.8.8.8", config.UPLOAD_SERVER):
        r = run(["ping", "-c", "1", "-W", "2", host], timeout=timeout + 2)
        if r and r.returncode == 0:
            return True
    return False


def _pick_internal():
    ifaces = iface_mod.get_interfaces()
    internal, _external = iface_mod.classify(ifaces)
    if internal:
        return internal[0][0]
    want = getattr(config, "INTERNAL_NAME", None) or "wl0"
    for name, _drv in ifaces:
        if name == want:
            return name
    # Last resort: any non-monitor wireless that isn't the obvious USB attack
    # adapter name used on this box (wlan1). Prefer wlan0/wl0.
    for prefer in ("wl0", "wlan0"):
        for name, _drv in ifaces:
            if name == prefer:
                return name
    return ifaces[0][0] if ifaces else None


def _active_connection(ifname):
    r = run(["nmcli", "-t", "-f", "UUID,NAME,DEVICE", "connection", "show",
             "--active"], timeout=8)
    if r and r.returncode == 0:
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) < 3:
                continue
            uuid, name, dev = parts[0], parts[1].replace("\\:", ":"), parts[2]
            if dev == ifname and name and name != "--":
                return {"uuid": uuid, "name": name}
    r = run(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "device", "show",
             ifname], timeout=8)
    if r and r.returncode == 0:
        for line in r.stdout.splitlines():
            if ":" in line:
                name = line.split(":", 1)[1].strip()
                if name and name != "--":
                    return {"uuid": None, "name": name}
    return None


def _restore_prior(ifname, prior, progress_cb=None):
    if progress_cb:
        progress_cb("restore home...")
    _log("restore prior=%s on %s" % (prior, ifname))
    run(["pkill", "-f", "wpa_supplicant.*-i %s" % ifname], timeout=5)
    _nm_manage(ifname, True)
    run(["nmcli", "device", "disconnect", ifname], timeout=15)
    time.sleep(0.4)

    if prior:
        for key in (prior.get("uuid"), prior.get("name")):
            if not key:
                continue
            r = run(["nmcli", "--wait", "35", "connection", "up", str(key),
                     "ifname", ifname], timeout=50)
            _log("restore up %s rc=%s" % (key, r.returncode if r else None))
            if r and r.returncode == 0:
                if progress_cb:
                    progress_cb("home: %s" % (prior.get("name") or "?")[:14])
                return True

    r = run(["nmcli", "--wait", "35", "device", "connect", ifname], timeout=50)
    if r and r.returncode == 0:
        if progress_cb:
            progress_cb("home: autoconnect")
        return True
    if restore_network():
        time.sleep(4)
        if progress_cb:
            progress_cb("home: nm restart")
        return True
    if progress_cb:
        progress_cb("home restore fail")
    return False


def connect(ssid, psk, progress_cb=None, stop_flag=None, bssid=None):
    """Connect using internal wifi. Primary path = wpa_supplicant.

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
    prior = _active_connection(name)
    prior_ssid = _associated_ssid(name)
    if prior and progress_cb:
        progress_cb("was: %s" % (prior.get("name") or prior_ssid or "?")[:16])
    _log("prior=%s ssid=%s -> %s" % (prior, prior_ssid, ssid))

    if prior_ssid and prior_ssid == ssid and _joined(name, ssid):
        if progress_cb:
            progress_cb("already on %s" % ssid[:14])
        return True, name, None

    def _fail(err):
        short = _short_err(err)
        if progress_cb:
            progress_cb("failed: %s" % short)
        _restore_prior(name, prior, progress_cb=progress_cb)
        return False, name, short

    if stop_flag and stop_flag.is_set():
        return _fail("cancelled")

    # Disconnect home so the switch is real.
    run(["nmcli", "device", "disconnect", name], timeout=15)
    _delete_target_profiles(ssid)
    time.sleep(0.3)

    # One quick nmcli shot (cheap when it works). Skip long retries — on this
    # box they almost always burn time before wpa succeeds anyway.
    if not _device_unmanaged(name):
        if progress_cb:
            progress_cb("nmcli try...")
        run(["nmcli", "device", "wifi", "rescan", "ifname", name], timeout=15)
        time.sleep(2.0)
        ok, err = _nmcli_quick(name, ssid, psk)
        if ok or _joined(name, ssid):
            if progress_cb:
                progress_cb("connected" if has_internet()
                            else "connected (no net)")
            return True, name, None
        last = err or "nmcli miss"
    else:
        last = "unmanaged"
        _log("skip nmcli — device unmanaged")

    if stop_flag and stop_flag.is_set():
        return _fail("cancelled")

    # Primary path that works on the Pi.
    ok, err = _wpa_connect(name, ssid, psk, bssid=None, progress_cb=progress_cb)
    if ok or _joined(name, ssid):
        if progress_cb:
            progress_cb("connected" if has_internet()
                        else "connected (no net)")
        return True, name, None

    # One more wpa attempt with BSSID pin if we have it.
    if bssid:
        if progress_cb:
            progress_cb("wpa+bssid...")
        ok, err = _wpa_connect(name, ssid, psk, bssid=bssid,
                               progress_cb=progress_cb)
        if ok or _joined(name, ssid):
            if progress_cb:
                progress_cb("connected" if has_internet()
                            else "connected (no net)")
            return True, name, None

    _log("FAILED %s (%s / %s)" % (ssid, last, err))
    return _fail(err or last)


def restore_network():
    for cmd in (["systemctl", "restart", "NetworkManager"],
                ["sudo", "systemctl", "restart", "NetworkManager"]):
        r = run(cmd, timeout=20)
        if r is not None and r.returncode == 0:
            return True
    return False


def current_ssid():
    r = run(["/usr/sbin/iwconfig"])
    if not r:
        return None
    m = re.search(r'ESSID:"(.*)"', r.stdout)
    return m.group(1) if m else None
