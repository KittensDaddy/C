# -*- coding: utf-8 -*-
"""Structured parser for wifite2 output.

Converts wifite's raw (ANSI-colored) stdout into discrete events consumed by
the UI status screen.

Events emitted through `handler`:
  on_targets(targets)         targets = [(name, bssid)]
  on_attack(essid, bssid)     attack starting against a target
  on_phase(essid, phase)      phase = 'handshake'|'crack'|'pmkid'|'wps'...
  on_handshake(essid, bssid)  WPA handshake captured
  on_cracked(essid, psk)      password recovered
  on_failed(essid)            attack failed for this target
  on_message(text)            generic status line
"""
import re

ANSI = re.compile(r"\x1b\[[0-?9;]*[mK]")


def strip_ansi(s):
    return ANSI.sub("", s)


class OutputParser:
    def __init__(self, handler=None):
        self.handler = handler or _null_handler
        self.current_essid = None
        self.current_bssid = None
        self.seen = {}

    def emit(self, name, *args):
        try:
            getattr(self.handler, name)(*args)
        except Exception:  # noqa: BLE001
            pass

    def feed(self, line):
        raw = strip_ansi(line).strip()
        if not raw:
            return
        self._handle(raw)

    def _handle(self, line):
        low = line.lower()
        # Target list table line:  " 5    MyNetwork   ...  -55dB  ..."
        m = re.search(r"^\s*\d+\s+(.{1,32}?)\s+\d+\s+\S+\s+([-]?\d+)dB", line)
        if m:
            return  # table row - handled by caller keeping a name list

        # Attacking a specific target
        m = re.search(r"Starting attacks against (.+?)\s*\((.+)\)\s*$", line)
        if not m:
            m = re.search(
                r"Starting attacks against (([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})",
                line)
        if m:
            mac = m.group(1)
            essid = m.group(2) if m.lastindex and m.lastindex >= 2 else None
            if not re.match(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", essid or ""):
                self.current_bssid = mac
                if essid and essid.lower() != "unknown":
                    self.current_essid = essid
            self.emit("on_attack", self.current_essid or "?", mac)
            return

        # Crack success - check BEFORE handshake so "Cracked WPA handshake"
        # doesn't falsely trigger the handshake handler.
        m = re.search(r"(?:cracked|password)[^:]*[:is]\s*['\"]?([^\s'\"\x1b]+)", line, re.I)
        if m and ("crack" in low or "password" in low):
            psk = m.group(1)
            if psk and psk.lower() not in ("none", "n/a", "null"):
                self.emit("on_cracked", self.current_essid, psk)
                return
        if "Cracked" in line and "N/A" in line.upper():
            self.emit("on_cracked", self.current_essid, None)
            return

        # Handshake capture
        if "WPA handshake" in line or "Handshake" in line:
            self.emit("on_handshake", self.current_essid, self.current_bssid)
            return

        # Failures
        if any(k in line for k in ("attack failed", "failed to crack",
                                   "could not crack", "FAILED")):
            self.emit("on_failed", self.current_essid)
            return

        # Attack phase markers
        if "pixie" in low:
            self.emit("on_phase", self.current_essid, "pixie")
            return
        if "pmkid" in low:
            self.emit("on_phase", self.current_essid, "pmkid")
            return
        if "cracking" in low or "aircrack" in low or "hashcat" in low:
            self.emit("on_phase", self.current_essid, "crack")
            return

        # "No targets found"
        if "no targets" in low or "nothing found" in low:
            self.emit("on_message", "No targets found")
            return

        self.emit("on_message", line[:64])


class _NullHandler:
    def __getattr__(self, item):
        return lambda *a, **k: None


def _null_handler(*a, **k):
    return None
