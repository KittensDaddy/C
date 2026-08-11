# -*- coding: utf-8 -*-
"""bettercap controller for post-connection LAN recon and BLE scanning.

We drive bettercap headless via a caplet that enables its REST API, then poll
/api/session for discovered hosts / BLE devices. Parsing is split into pure
functions (parse_hosts / parse_ble) so they can be unit-tested without the tool.

Graceful everywhere: if bettercap is missing or the API is unreachable, starts
fail cleanly and pollers return [].
"""
import os
import json
import time
import base64
import shutil
import subprocess
import urllib.request

import config

API_ADDR = "127.0.0.1"
API_PORT = 8081
API_USER = "wifibox"
API_PASS = "wifibox"


def available():
    return shutil.which("bettercap") is not None


def _caplet(ble):
    """Write and return a caplet path enabling the REST API + recon modules."""
    lines = [
        "set api.rest.address %s" % API_ADDR,
        "set api.rest.port %d" % API_PORT,
        "set api.rest.username %s" % API_USER,
        "set api.rest.password %s" % API_PASS,
        "api.rest on",
    ]
    if ble:
        lines.append("ble.recon on")
    else:
        lines += ["net.probe on", "net.recon on"]
    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    path = os.path.join(config.DATA_DIR, "wifibox_%s.cap" % ("ble" if ble else "lan"))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return path


# -- pure parsers (testable without bettercap) ---------------------------------
def parse_hosts(session):
    """LAN hosts from an /api/session dict -> list of compact dicts."""
    out = []
    for h in (session or {}).get("lan", {}).get("hosts", []) or []:
        out.append({
            "ip": h.get("ipv4") or h.get("ip") or "?",
            "mac": h.get("mac", "?"),
            "name": h.get("hostname") or "",
            "vendor": (h.get("vendor") or "")[:14],
        })
    return out


def parse_ble(session):
    """BLE devices from an /api/session dict -> list of compact dicts."""
    out = []
    for d in (session or {}).get("ble", {}).get("devices", []) or []:
        adv = d.get("advertisement", {}) if isinstance(d.get("advertisement"), dict) else {}
        out.append({
            "mac": d.get("mac", "?"),
            "name": d.get("name") or adv.get("name") or "",
            "vendor": (d.get("vendor") or "")[:14],
            "rssi": d.get("rssi"),
        })
    # strongest signal first when RSSI is present
    out.sort(key=lambda x: (x["rssi"] is None, -(x["rssi"] or -999)))
    return out


class Recon:
    """Runs a bettercap recon session and polls it for results."""

    def __init__(self, iface=None, ble=False):
        self.iface = iface
        self.ble = ble
        self.proc = None
        self.error = None

    def start(self):
        if not available():
            self.error = "bettercap not installed"
            return False
        cmd = ["sudo", "bettercap", "-no-colors", "-caplet", _caplet(self.ble)]
        if self.iface:
            cmd[1:1] = ["-iface", self.iface]
        try:
            self.proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            return False
        time.sleep(2.0)     # let the REST API come up
        return True

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                time.sleep(0.5)
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self.proc = None

    def _session(self):
        url = "http://%s:%d/api/session" % (API_ADDR, API_PORT)
        token = base64.b64encode(("%s:%s" % (API_USER, API_PASS)).encode()).decode()
        req = urllib.request.Request(url, headers={"Authorization": "Basic " + token})
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.load(r)

    def poll(self):
        """Return the current result list (hosts or BLE devices)."""
        try:
            s = self._session()
        except Exception:  # noqa: BLE001
            return []
        return parse_ble(s) if self.ble else parse_hosts(s)
