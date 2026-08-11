# -*- coding: utf-8 -*-
"""Typed contracts for wifite2 attacks: parsing, requests, results."""
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Dict, List, Optional

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
BSSID = r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"


class EventType(str, Enum):
    SCAN = "scan"
    TARGET = "target"
    PHASE = "phase"
    HANDSHAKE = "handshake"
    PMKID = "pmkid"
    CRACKING = "cracking"
    CRACKED = "cracked"
    FAILED = "failed"
    SKIPPED = "skipped"
    CLIENT = "client"
    DEAUTH = "deauth"
    MESSAGE = "message"
    PROCESS_EXIT = "process_exit"


@dataclass(frozen=True)
class AttackRequest:
    """What the user asked for — never reads stale globals."""
    interface: str
    preset: Optional[Dict[str, Any]] = None
    target_essid: Optional[str] = None
    target_bssid: Optional[str] = None
    exclusions: List[str] = field(default_factory=list)
    resume_latest: bool = False
    clean_sessions: bool = False
    # Frozen snapshot of config options at request time
    attack_modes: List[Dict[str, Any]] = field(default_factory=list)
    timing: List[Dict[str, Any]] = field(default_factory=list)
    filters: List[Dict[str, Any]] = field(default_factory=list)
    interface_opts: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AttackResult:
    """What happened — never mutates after creation."""
    ok: bool
    error: Optional[str] = None
    cancelled: bool = False
    exit_code: Optional[int] = None
    cracked: List[Dict[str, Any]] = field(default_factory=list)
    handshakes: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    command: Optional[str] = None
    monitor_iface: Optional[str] = None
    elapsed: float = 0.0


@dataclass(frozen=True)
class AttackEvent:
    type: EventType
    essid: Optional[str] = None
    bssid: Optional[str] = None
    phase: Optional[str] = None
    detail: Optional[str] = None
    credential: Optional[str] = None
    targets: Optional[int] = None
    clients: Optional[int] = None
    current: Optional[int] = None
    total: Optional[int] = None
    exit_code: Optional[int] = None
    cancelled: bool = False
    raw: Optional[str] = field(default=None, repr=False)

    def as_dict(self) -> Dict[str, Any]:
        value = {k: v for k, v in self.__dict__.items()
                 if v is not None and k != "raw"}
        value["type"] = self.type.value
        if self.credential is not None:
            value["psk"] = self.credential
        return value

    @property
    def compact(self) -> str:
        name = (self.essid or "?")[:13]
        if self.type == EventType.HANDSHAKE:
            return "HS %s" % name
        if self.type == EventType.PMKID:
            return "PMKID FOUND"
        if self.type == EventType.CRACKED:
            return "KEY %s" % name
        if self.type == EventType.DEAUTH:
            if self.current is not None and self.total is not None:
                return "WPA DEAUTH %d/%d" % (self.current, self.total)
            return "WPA DEAUTH"
        if self.type == EventType.FAILED:
            return "FAIL %s" % name
        if self.type == EventType.SKIPPED:
            return "SKIP %s" % name
        if self.type == EventType.PHASE:
            return (self.phase or self.detail or "ATTACK")[:20]
        if self.type == EventType.SCAN:
            return "SCAN %d AP / %d STA" % (self.targets or 0,
                                            self.clients or 0)
        return (self.detail or self.raw or self.type.value.upper())[:20]


def strip_ansi(value: Optional[str]) -> str:
    return ANSI.sub("", value or "")


class OutputParser:
    """Return typed events. Unknown lines remain visible as MESSAGE events."""

    def __init__(self):
        self.current_essid = None
        self.current_bssid = None

    def feed(self, chunk: str) -> List[AttackEvent]:
        events = []
        # Wifite uses both newline and carriage return for live progress.
        for value in re.split(r"[\r\n]+", strip_ansi(chunk)):
            line = value.strip()
            if line:
                events.append(self._parse(line))
        return events

    def _event(self, kind, line, **values):
        values.setdefault("essid", self.current_essid)
        values.setdefault("bssid", self.current_bssid)
        return AttackEvent(kind, raw=line, **values)

    def _parse(self, line: str) -> AttackEvent:
        match = re.search(
            r"\[\+\]\s+Scanning.*?Targets:\s*(\d+).*?Clients:\s*(\d+)",
            line, re.I)
        if not match:
            match = re.search(
                r"Scanning\s+\(native\).*?Found\s+(\d+)\s+target.*?(\d+)\s+client",
                line, re.I)
        if match:
            return self._event(EventType.SCAN, line, targets=int(match.group(1)),
                               clients=int(match.group(2)))

        match = re.search(r"found target\s+(%s)\s+\((.*?)\)" % BSSID,
                          line, re.I)
        if match:
            self.current_bssid, self.current_essid = match.group(1), match.group(2)
            return self._event(EventType.TARGET, line)

        match = re.search(
            r"\((\d+)/(\d+)\)\s+Starting attacks against\s+(%s)\s+\((.*?)\)"
            % BSSID, line, re.I)
        if match:
            self.current_bssid, self.current_essid = match.group(3), match.group(4)
            return self._event(EventType.TARGET, line,
                               current=int(match.group(1)),
                               total=int(match.group(2)))

        match = re.search(
            r"^(.+?)\s+\([-+]?\d+\s*dB\)\s+(WPS|WPA|WEP)\s+(.+?):\s*(.*)$",
            line, re.I)
        if match:
            self.current_essid = match.group(1).strip()
            phase = ("%s %s" % (match.group(2), match.group(3))).upper()
            detail = match.group(4).strip()
            deauth = re.search(r"(?:deauth(?:ing)?).*?(\d+)\s*(?:/|of)\s*(\d+)",
                               detail, re.I)
            if "DEAUTH" in phase or deauth:
                return self._event(
                    EventType.DEAUTH, line, phase=phase, detail=detail,
                    current=int(deauth.group(1)) if deauth else None,
                    total=int(deauth.group(2)) if deauth else None)
            return self._event(EventType.PHASE, line, phase=phase, detail=detail)

        if re.search(r"\[\+\]\s+Captured.*handshake", line, re.I):
            return self._event(EventType.HANDSHAKE, line)
        if re.search(r"\[\+\]\s+Captured\s+PMKID", line, re.I):
            return self._event(EventType.PMKID, line)
        if re.search(r"\[\+\]\s+Cracking\s+(?:WPA|WPA/WPA2|WPA3)", line, re.I):
            return self._event(EventType.CRACKING, line)

        match = re.search(r"\[\+\]\s+Cracked WPS PIN:\s*(\S+)(?:\s+PSK:\s*(\S+))?",
                          line, re.I)
        if match:
            return self._event(EventType.CRACKED, line,
                               credential=match.group(2) or match.group(1))
        match = re.search(
            r"\[\+\]\s+Cracked (?:WPA(?:/WPA2|3-SAE)?\s+)?(?:Handshake\s+)?(?:Key|PIN):\s*(\S+)",
            line, re.I)
        if not match:
            match = re.search(r"^(?:\[C\])?\s*Key:\s*(\S+)", line, re.I)
        if match:
            return self._event(EventType.CRACKED, line,
                               credential=match.group(1))

        if re.search(r"\[!\].*(?:FAILED|Failed|Target timeout|No attacks succeeded)",
                     line, re.I):
            return self._event(EventType.FAILED, line, detail=line[:48])
        match = re.search(r"\[!\]\s+Skipping\s+.*?attack on\s+(.+?)(?:\s+because|$)",
                          line, re.I)
        if match:
            return self._event(EventType.SKIPPED, line,
                               essid=match.group(1).strip())
        match = re.search(r"Discovered (?:new )?client:\s*(%s|\S+)" % BSSID,
                          line, re.I)
        if match:
            return self._event(EventType.CLIENT, line, detail=match.group(1))
        match = re.search(r"Deauth(?:ing)?[^0-9]*(\d+)\s*(?:/|of)\s*(\d+)",
                          line, re.I)
        if match:
            return self._event(EventType.DEAUTH, line,
                               current=int(match.group(1)),
                               total=int(match.group(2)))
        return self._event(EventType.MESSAGE, line, detail=line[:48])
