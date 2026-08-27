# -*- coding: utf-8 -*-
"""Wireless interface discovery and exception-safe monitor-mode control."""
from contextlib import contextmanager
import os
import shutil
import subprocess
import time

import config

# Don't hammer USB recover — unbind/buspower was adding 10–20s to every miss.
_last_recover_ts = 0.0
_RECOVER_COOLDOWN = 60.0


def run(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _driver_of(ifname):
    """Prefer sysfs (ms); ethtool can stall several seconds on some USB NICs."""
    for rel in (
        "/sys/class/net/%s/device/driver" % ifname,
        "/sys/class/net/%s/device/driver/module" % ifname,
    ):
        try:
            return os.path.basename(os.readlink(rel))
        except OSError:
            pass
    out = run(["/usr/sbin/ethtool", "-i", ifname], timeout=1)
    if not out:
        return "unknown"
    for line in out.stdout.splitlines():
        if line.lower().startswith("driver"):
            return line.partition(":")[2].strip() or "unknown"
    return "unknown"


def _ls_interfaces():
    """Fast path: sysfs only (no iwconfig/ethtool round-trips)."""
    result = []
    try:
        for name in os.listdir("/sys/class/net"):
            if os.path.exists("/sys/class/net/%s/wireless" % name):
                result.append((name, _driver_of(name)))
    except OSError:
        pass
    return result


def _iwconfig_scan():
    out = run(["/usr/sbin/iwconfig"], timeout=2)
    if not out:
        return []
    result = []
    for line in out.stdout.splitlines():
        if "IEEE 802.11" in line and line.split():
            name = line.split()[0]
            result.append((name, _driver_of(name)))
    return list(dict((item[0], item) for item in result).values())


def get_interfaces():
    # sysfs first — was ~10s when iwconfig/ethtool stalled on a wedged USB NIC.
    return _ls_interfaces() or _iwconfig_scan()


def classify(ifaces):
    internal, external = [], []
    for item in ifaces:
        (internal if item[1] == config.INTERNAL_DRIVER else external).append(item)
    return internal, external


def pick_external(ifaces=None):
    ifaces = ifaces if ifaces is not None else get_interfaces()
    return (classify(ifaces)[1] or [None])[0]


def _log(msg):
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write("[iface] %s\n" % msg)
    except Exception:  # noqa: BLE001
        pass


def _usb_wireless_present():
    """True if any wireless netdev is on a USB bus (the external adapter)."""
    try:
        for name in os.listdir("/sys/class/net"):
            wireless = "/sys/class/net/%s/wireless" % name
            device = "/sys/class/net/%s/device" % name
            if not os.path.exists(wireless):
                continue
            try:
                path = os.path.realpath(device)
            except OSError:
                continue
            if "/usb" in path:
                return True
    except OSError:
        pass
    return False


def _quick_ext():
    return pick_external(get_interfaces())


def _read_sys(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _ext_usb_device():
    """Locate the external WiFi's USB device as {name, bus, dev}, or None.

    Prefer the wireless netdev's device path; fall back to any Realtek (0bda)
    device still on the bus — that catches the early wedge where the netdev is
    gone but the device hasn't dropped off the bus yet.
    """
    def _dev_from(path):
        segs = path.rstrip("/").split("/")
        for i, seg in enumerate(segs):
            if seg.startswith("usb") and i + 1 < len(segs):
                d = segs[i + 1]
                if ":" in d:
                    d = d.split(":")[0]
                bus = _read_sys("/sys/bus/usb/devices/%s/busnum" % d)
                num = _read_sys("/sys/bus/usb/devices/%s/devnum" % d)
                if bus and num:
                    return {"name": d, "bus": bus, "dev": num}
        return None

    try:
        for name in os.listdir("/sys/class/net"):
            if not os.path.exists("/sys/class/net/%s/wireless" % name):
                continue
            try:
                path = os.path.realpath("/sys/class/net/%s/device" % name)
            except OSError:
                continue
            if "/usb" in path:
                found = _dev_from(path)
                if found:
                    return found
    except OSError:
        pass

    # Fallback: any Realtek USB device still on the bus (wedge, no netdev).
    try:
        for d in os.listdir("/sys/bus/usb/devices"):
            if ":" in d:
                continue
            vid = _read_sys("/sys/bus/usb/devices/%s/idVendor" % d)
            if vid and vid.lower() in ("0bda", "bda"):
                bus = _read_sys("/sys/bus/usb/devices/%s/busnum" % d)
                num = _read_sys("/sys/bus/usb/devices/%s/devnum" % d)
                if bus and num:
                    return {"name": d, "bus": bus, "dev": num}
    except OSError:
        pass
    return None


def _usb_reset(dev):
    """Reset a wedged-but-still-enumerated device: `authorized` toggle, then
    usbreset (USBDEVFS_RESET). Returns True if either ran."""
    ok = False
    auth = "/sys/bus/usb/devices/%s/authorized" % dev["name"]
    if os.path.exists(auth):
        r = run(["sh", "-c",
                 "echo 0 > %s; sleep 1; echo 1 > %s" % (auth, auth)], timeout=8)
        ok = r is not None
    usbreset = shutil.which("usbreset") or (
        "/usr/bin/usbreset" if os.path.exists("/usr/bin/usbreset") else None)
    if usbreset:
        # usbreset takes "BBB/DDD", not a /dev/bus/... path.
        r = run([usbreset, "%s/%s" % (dev["bus"], dev["dev"])], timeout=8)
        ok = ok or (r is not None)
    return ok


def recover_external_usb(wait=3.0, force=False):
    """Revive a missing external USB Wi‑Fi — light steps first, stop early.

    Order matters: firmware re-download (module reload) -> USB reset while the
    device is still enumerable -> unbind/rebind -> bus power. On the Pi Zero 2 W
    VBUS is hardwired and the dwc_otg controller can't be hot-reset, so once the
    device has dropped off the bus (error -71) only a reboot recovers it — the
    module options in /etc/modprobe.d/rtw88.conf are what actually prevent that.
    """
    global _last_recover_ts
    ext = _quick_ext()
    if ext:
        return ext

    now = time.time()
    if not force and (now - _last_recover_ts) < _RECOVER_COOLDOWN:
        _log("recover skipped (cooldown %.0fs)" % (
            _RECOVER_COOLDOWN - (now - _last_recover_ts)))
        return None
    _last_recover_ts = now
    _log("external missing — USB recover")

    dev = _ext_usb_device()

    # 1) Full rtw88 stack reload — re-downloads firmware when the netdev is
    #    gone but the device is still on the bus (~1-2s).
    run(["modprobe", "-r", "rtw88_8822bu", "rtw88_usb", "rtw88_8822b",
         "rtw88_core"], timeout=8)
    run(["modprobe", "rtw88_8822bu"], timeout=8)
    time.sleep(1.0)
    ext = _quick_ext()
    if ext:
        _log("external recovered after modprobe reload")
        return ext

    # 2) USB reset (authorized toggle + usbreset) — early wedge, device present.
    if dev and _usb_reset(dev):
        time.sleep(1.0)
        ext = _quick_ext()
        if ext:
            _log("external recovered after USB reset")
            return ext

    # 3) Unbind/rebind the USB device.
    if dev:
        run(["sh", "-c",
             "echo %s > /sys/bus/usb/drivers/usb/unbind 2>/dev/null; "
             "sleep 1; "
             "echo %s > /sys/bus/usb/drivers/usb/bind 2>/dev/null"
             % (dev["name"], dev["name"])], timeout=8)
        time.sleep(1.0)
        ext = _quick_ext()
        if ext:
            _log("external recovered after unbind: %s" % (ext,))
            return ext

    # 4) Bus power only if still gone (last resort; VBUS hardwired on Zero 2 W).
    buspower = "/sys/devices/platform/soc/3f980000.usb/buspower"
    run(["sh", "-c",
         "if [ -w %s ]; then echo 0 > %s; sleep 2; echo 1 > %s; fi"
         % (buspower, buspower, buspower)], timeout=8)
    run(["modprobe", "rtw88_8822bu"], timeout=5)

    deadline = time.time() + max(1.0, float(wait))
    while time.time() < deadline:
        ext = _quick_ext()
        if ext:
            _log("external recovered: %s" % (ext,))
            return ext
        time.sleep(0.4)
    _log("external still missing after USB recover — reboot needed")
    return None


def _reload_rtw88(ifname):
    """Reload the rtw88 stack (re-downloads firmware) and leave the netdev
    up in managed mode. Shared by refresh_external and reload_managed —
    the difference is only what the caller does with the interface after.
    Returns the (possibly new) interface name, or None on failure."""
    name = iface_name(ifname)
    disable_monitor(name)
    time.sleep(0.5)
    run(["modprobe", "-r", "rtw88_8822bu", "rtw88_usb", "rtw88_8822b",
         "rtw88_core"], timeout=10)
    time.sleep(1.0)
    run(["modprobe", "rtw88_8822bu"], timeout=10)
    time.sleep(2.5)          # let the netdev re-enumerate
    ext = _quick_ext()
    if not ext:
        return None
    name = iface_name(ext)
    run(["ip", "link", "set", name, "up"], timeout=3)
    return name


def refresh_external(ifname):
    """Reload the rtw88 stack to re-download firmware, then re-enter monitor.

    The RTL8822BU firmware wedges after sustained monitor/reaver + OneShot
    managed-mode switching. Reloading the module (and thus re-downloading the
    firmware) between targets clears that accumulated stress before it becomes
    a hard wedge that only a reboot fixes. Returns the new monitor iface name
    (or the managed name if monitor mode can't be re-entered), or None.
    """
    name = _reload_rtw88(ifname)
    if not name:
        return None
    mon = enable_monitor(name)
    _log("refresh_external -> %s" % (mon or name))
    return mon or name


def reload_managed(ifname):
    """Reload the rtw88 stack and leave the netdev in managed mode (NOT
    monitor). hcxdumptool arms monitor mode itself and reliably fails
    ("driver is broken", 0 packets captured) when handed an interface some
    other tool (iw/iwconfig/airmon-ng) already pre-armed into monitor —
    confirmed live: identical hcxdumptool invocation goes from a real capture
    to 0 packets purely based on whether the interface started managed or was
    pre-armed. The accumulated corruption from earlier monitor-mode attacks
    (reaver/airodump) in the same run also has to be cleared for this to work,
    which is what the reload itself does. Returns the managed iface name, or
    None on failure."""
    name = _reload_rtw88(ifname)
    _log("reload_managed -> %s" % name)
    return name


def ensure_external(ifaces=None, recover=False):
    """Return external iface. Recover is OFF by default (was making UI/attacks crawl).

    Pass recover=True only when the caller already knows the card is gone and
    is willing to wait a few seconds (e.g. mid-attack after a wedge).
    """
    ifaces = ifaces if ifaces is not None else get_interfaces()
    ext = pick_external(ifaces)
    if ext:
        return ext
    if not recover:
        return None
    return recover_external_usb()


def iface_name(iface):
    return iface[0] if isinstance(iface, tuple) else iface


def is_in_monitor(ifname):
    out = run(["/usr/sbin/iwconfig", iface_name(ifname)], timeout=2)
    return bool(out and "Mode:Monitor" in out.stdout)


def lock_channel(mon, channel):
    """Best-effort fixed-channel lock before TX/RX (reaver/airodump/aireplay)."""
    if not channel:
        return False
    name = iface_name(mon)
    ch = str(channel)
    for cmd in (
        ["iw", "dev", name, "set", "channel", ch],
        ["/usr/sbin/iw", "dev", name, "set", "channel", ch],
        ["iwconfig", name, "channel", ch],
        ["/usr/sbin/iwconfig", name, "channel", ch],
    ):
        res = run(cmd, timeout=3)
        if res is not None and res.returncode == 0:
            try:
                with open(config.LOG_FILE, "a") as f:
                    f.write("[iface] channel lock: %s -> %s\n" % (name, ch))
            except Exception:  # noqa: BLE001
                pass
            return True
    try:
        with open(config.LOG_FILE, "a") as f:
            f.write("[iface] channel lock failed: %s ch=%s\n" % (name, ch))
    except Exception:  # noqa: BLE001
        pass
    return False


def enable_monitor(ifname):
    """Return the actual monitor interface name, or None on failure."""
    name = iface_name(ifname)
    if is_in_monitor(name):
        return name
    before = {item[0] for item in get_interfaces()}
    result = run(["/usr/sbin/airmon-ng", "start", name], timeout=10)
    if result is not None:
        time.sleep(0.2)
        candidates = [name + "mon", name]
        candidates += [n for n, _ in get_interfaces() if n not in before]
        for candidate in candidates:
            if is_in_monitor(candidate):
                return candidate
    if run(["ip", "link", "set", name, "down"], timeout=3) is not None:
        changed = run(["iw", "dev", name, "set", "type", "monitor"], timeout=3)
        run(["ip", "link", "set", name, "up"], timeout=3)
        if changed is not None and changed.returncode == 0 and is_in_monitor(name):
            return name
    return None


def disable_monitor(ifname):
    name = iface_name(ifname)
    # Airmon-created names should be stopped before attempting managed mode.
    stopped = run(["/usr/sbin/airmon-ng", "stop", name], timeout=10)
    if stopped is not None and stopped.returncode == 0:
        return True
    down = run(["ip", "link", "set", name, "down"], timeout=3)
    changed = run(["iw", "dev", name, "set", "type", "managed"], timeout=3)
    run(["ip", "link", "set", name, "up"], timeout=3)
    return bool(down is not None and changed is not None and
                changed.returncode == 0)


@contextmanager
def monitor_mode(ifname, required=True):
    """Yield the actual interface and always restore it after the operation."""
    original = iface_name(ifname)
    monitor = enable_monitor(original)
    if required and not monitor:
        raise RuntimeError("could not enable monitor mode on %s" % original)
    active = monitor or original
    config.Runtime.monitor_iface = active
    try:
        yield active
    finally:
        try:
            if monitor:
                disable_monitor(active)
        finally:
            config.Runtime.monitor_iface = None
