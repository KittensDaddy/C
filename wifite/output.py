# -*- coding: utf-8 -*-
"""Structured parser for wifite2 stdout (kimocoder/wifite2).

Converts wifite's ANSI-colored output into discrete events for the UI.
Based on actual wifite2 source: Color.s() token expansion, target.to_str(),
attack progress via Color.pattack(), and handshake/crack/fail messages.
"""
import re

ANSI = re.compile(r"\x1b\[[0-?9;]*[mK]")


def strip_ansi(s):
    if s is None:
        return ""
    return ANSI.sub("", s)


# Color tokens that wifite2 emits (expanded by Color.s() before printing)
# After expansion: {+} -> " [\x1b[2m[\x1b[0m\x1b[32m+\x1b[0m\x1b[2m]\x1b[0m"
# After strip_ansi: "[+]"
# {!} -> "[!]", {?} -> "[?]"


class OutputParser:
    """Feeds lines, calls handler events."""

    def __init__(self, handler=None):
        self.handler = handler or _null_handler
        self._current_essid = None
        self._current_bssid = None
        self._scanning = False
        self._target_count = 0
        self._client_count = 0

    def emit(self, name, *args):
        try:
            getattr(self.handler, name)(*args)
        except Exception:
            pass

    def feed(self, line):
        raw = strip_ansi(line).strip()
        if not raw:
            return
        self._parse(raw)

    def _parse(self, line):
        # ---- Scanning status (carriage-return line) ----
        # "[+] Scanning [00:12] Targets: 5 Clients: 1 | Ctrl+C to stop"
        m = re.search(
            r"\[\+\]\s+Scanning\s.*?Targets:\s*(\d+).*?Clients:\s*(\d+)",
            line)
        if m:
            self._scanning = True
            self._target_count = int(m.group(1))
            self._client_count = int(m.group(2))
            self.emit("on_scan", self._target_count, self._client_count)
            return

        # "[+] Scanning (native). Found N target(s), M client(s)..."
        m = re.search(
            r"\[\+\]\s+Scanning\s+\(native\).*?Found\s+(\d+)\s+target.*?(\d+)\s+client",
            line)
        if m:
            self._scanning = True
            self._target_count = int(m.group(1))
            self._client_count = int(m.group(2))
            self.emit("on_scan", self._target_count, self._client_count)
            return

        # ---- Found a specific target during scan ----
        # "[+] found target AA:BB:CC:DD:EE:FF (MyNetwork)"
        m = re.search(
            r"\[\+\]\s+found target\s+([0-9a-fA-F:]{17})\s+\((.+)\)", line)
        if m:
            self.emit("on_found", m.group(2), m.group(1))
            return

        # ---- Attacking a target ----
        # "[+] (1/3) Starting attacks against AA:BB:CC:DD:EE:FF (MyNetwork)"
        m = re.search(
            r"\[\+\]\s+\((\d+)/(\d+)\)\s+Starting attacks against\s+"
            r"([0-9a-fA-F:]{17})\s+\((.+)\)", line)
        if m:
            self._current_bssid = m.group(3)
            self._current_essid = m.group(4)
            self.emit("on_attack", self._current_essid, self._current_bssid)
            return

        # ---- WPA Handshake captured ----
        # "[+] Captured handshake" (includes [DUAL] suffix)
        if re.search(r"\[\+\]\s+Captured handshake", line):
            self.emit("on_handshake", self._current_essid)
            return

        # ---- PMKID captured ----
        if re.search(r"\[\+\]\s+Captured PMKID", line):
            self.emit("on_pmkid", self._current_essid)
            return

        # ---- Attack phase from Color.pattack output ----
        # Format: "ESSID (-55db) WPS Pixie-Dust: [00:30] Sending M4"
        m = re.search(
            r"^(.+?)\s+\([-+]?\d+db\)\s+(\S+)\s+(.+?):\s*(.*)", line)
        if m:
            essid = m.group(1)
            atype = m.group(2)   # "WPS", "WPA", "WEP"
            name = m.group(3)    # "Pixie-Dust", "Handshake", etc.
            status = m.group(4)
            self._current_essid = essid
            self.emit("on_phase", essid, "%s %s" % (atype, name), status)
            return

        # ---- Cracked WPS PIN + PSK ----
        # "[+] Cracked WPS PIN: 12345678 PSK: password"
        m = re.search(r"\[\+\]\s+Cracked WPS PIN:\s*(\S+)\s+PSK:\s*(\S+)", line)
        if m:
            self.emit("on_cracked", self._current_essid, m.group(2))
            return

        # "[+] Cracked WPS PIN: 12345678" (no PSK)
        m = re.search(r"\[\+\]\s+Cracked WPS PIN:\s*(\S+)", line)
        if m:
            self.emit("on_cracked", self._current_essid, None)
            return

        # ---- Cracked WPA handshake key ----
        # "[+] Cracked WPA Handshake Key: password"
        # "[+] Cracked WPA/WPA2 Handshake Key: password"
        m = re.search(
            r"\[\+\]\s+Cracked (?:WPA(?:/WPA2|3-SAE)?\s+)?Handshake\s+Key:\s*(\S+)",
            line)
        if m:
            self.emit("on_cracked", self._current_essid, m.group(1))
            return

        # ---- PMKID crack: "Key: password" (standalone or after crack message) ----
        m = re.search(r"^(?:\[C\])?\s*Key:\s*(\S+)", line)
        if m:
            self.emit("on_cracked", self._current_essid, m.group(1))
            return

        # ---- WEP crack: "Cracked WEP Key: xx:xx:xx:xx:xx" ----
        m = re.search(r"\[\+\]\s+Cracked (?:WPA(?:/WPA2)?\s+)?Handshake\s+Key:\s*(\S+)", line)
        if m:
            self.emit("on_cracked", self._current_essid, m.group(1))
            return

        # ---- Bully/PIN crack ----
        # "[+] Cracked PIN: 12345678"
        m = re.search(r"\[\+\]\s+Cracked (?:PIN|Key):\s*(\S+)", line)
        if m:
            self.emit("on_cracked", self._current_essid, m.group(1))
            return

        # ---- Attack failures ----
        # "[!] WPA handshake capture FAILED: Timed out after 240 seconds"
        # "[!] Failed: ..."
        # "[!] Target timeout: ..."
        if re.search(r"\[\!\]\s+(?:WPA\s+.*FAILED|Failed|Target timeout)", line):
            self.emit("on_failed", self._current_essid)
            return

        # ---- Skipping target ----
        # "[!] Skipping WPA-Handshake attack on ESSID because --wps-only is set"
        m = re.search(r"\[\!\]\s+Skipping\s.*attack on\s+(\S+)", line)
        if m:
            self.emit("on_skipped", m.group(1))
            return

        # ---- Client discovered ----
        m = re.search(r"Discovered (?:new )?client:\s*(.+)", line)
        if m:
            self.emit("on_client", m.group(1))
            return

        # ---- Deauth in progress ----
        if "Deauth" in line and "Deauthing" and not "deauths" in line:
            self.emit("on_deauth", self._current_essid)
            return

        # ---- Cracking message ----
        if re.search(r"\[\+\]\s+Cracking\s+(?:WPA|WPA/WPA2|WPA3)\s+Handshake", line):
            self.emit("on_cracking", self._current_essid)
            return

        # Any other non-empty line
        self.emit("on_message", line[:72])


class _NullHandler:
    def __getattr__(self, item):
        return lambda *a, **k: None


_null_handler = _NullHandler()
