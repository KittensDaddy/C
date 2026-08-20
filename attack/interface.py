# -*- coding: utf-8 -*-
"""Wireless interface discovery and exception-safe monitor-mode control."""
from contextlib import contextmanager
import os
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


def recover_external_usb(wait=3.0, force=False):
    """Revive a missing external USB Wi‑Fi — light steps first, stop early.

    Previously always ran unbind(2s)+buspower(3s)+modprobe+8s wait even when
    unnecessary. Now: cooldown, check after each step, short waits.
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
    _log("external missing — USB recover (fast)")

    buspower = "/sys/devices/platform/soc/3f980000.usb/buspower"
    dev = "1-1"
    try:
        for name in os.listdir("/sys/bus/usb/devices"):
            if name.startswith("1-") and ":" not in name and name != "1-0":
                dev = name
                break
    except OSError:
        pass

    # 1) Soft: reload driver only (~1s)
    run(["modprobe", "-r", "rtw88_8822bu"], timeout=5)
    run(["modprobe", "rtw88_8822bu"], timeout=5)
    time.sleep(0.8)
    ext = _quick_ext()
    if ext:
        _log("external recovered after modprobe: %s" % (ext,))
        return ext

    # 2) Unbind/rebind USB device (~1s sleep)
    run(["sh", "-c",
         "echo %s > /sys/bus/usb/drivers/usb/unbind 2>/dev/null; "
         "sleep 1; "
         "echo %s > /sys/bus/usb/drivers/usb/bind 2>/dev/null" % (dev, dev)],
        timeout=8)
    time.sleep(0.8)
    ext = _quick_ext()
    if ext:
        _log("external recovered after unbind: %s" % (ext,))
        return ext

    # 3) Bus power only if still gone (last resort, ~2s)
    run(["sh", "-c",
         "if [ -w %s ]; then echo 0 > %s; sleep 1; echo 1 > %s; fi"
         % (buspower, buspower, buspower)], timeout=8)
    run(["modprobe", "rtw88_8822bu"], timeout=5)

    deadline = time.time() + max(1.0, float(wait))
    while time.time() < deadline:
        ext = _quick_ext()
        if ext:
            _log("external recovered: %s" % (ext,))
            return ext
        time.sleep(0.4)
    _log("external still missing after USB recover — replug adapter")
    return None


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
