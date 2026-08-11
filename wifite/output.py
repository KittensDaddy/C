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
    signal: Optional[int] = None
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
        name = (self.essid or "?")[:15]
        if self.type == EventType.SCAN:
            return "SCAN %d/%d" % (self.targets or 0, self.clients or 0)
        if self.type == EventType.TARGET:
            if self.current and self.total:
                return ">> %s (%d/%d)" % (name, self.current, self.total)
            return "AP %s" % name
        if self.type == EventType.HANDSHAKE:
            return "HS %s" % name
        if self.type == EventType.PMKID:
            return "PMKID %s" % name
        if self.type == EventType.CRACKING:
            return "CRACK %s..." % name
        if self.type == EventType.CRACKED:
            cred = self.credential or "?"
            return "KEY %s=%s" % (name, cred[:10])
        if self.type == EventType.FAILED:
            return "FAIL %s" % name
        if self.type == EventType.SKIPPED:
            return "SKIP %s" % name
        if self.type == EventType.CLIENT:
            return "STA %s" % (self.detail or "?")
        if self.type == EventType.DEAUTH:
            if self.current and self.total:
                return "DEAUTH %d/%d %s" % (self.current, self.total, name)
            return "DEAUTH %s" % name
        if self.type == EventType.PHASE:
            return self._phase_compact()
        return (self.detail or self.raw or "")[:20]

    def _phase_compact(self) -> str:
        """Minimal single-line phase description for tiny LCD."""
        name = (self.essid or "?")[:10]
        phase = (self.phase or "").upper()
        detail = self.detail or ""
        # WPS Pixie-Dust: "[00:25] Sending M4" → "PIXIE M4 25s"
        if "PIXIE" in phase:
            m = re.search(r"\[(\d+):(\d+)\]", detail)
            if m:
                mm, ss = m.group(1), m.group(2)
                rest = re.sub(r"\[[\d:]+\]\s*", "", detail).strip()[:8].rstrip()
                return "PIXIE %s %s:%s" % (rest, mm, ss)
            return "PIXIE %s" % (detail[:10]) if detail else "PIXIE %s" % name
        # WPS PIN: "[01:23] Trying PIN" → "PIN TRY 1:23"
        if "WPS" in phase and "PIN" in phase:
            m = re.search(r"\[(\d+):(\d+)\]", detail)
            if m:
                return "PIN %s %s:%s" % (detail[:6].strip(" []"), m.group(1), m.group(2))
            return "PIN %s" % (detail[:10]) if detail else "PIN %s" % name
        # WPA Handshake: "Listening. (clients:2, deauth:yes, timeout:03:42)"
        if "HANDSHAKE" in phase or ("WPA" in phase and "DEAUTH" not in phase):
            m_c = re.search(r"clients[:=](\d+)", detail, re.I)
            m_t = re.search(r"timeout[:=]\s*(\d+):(\d+)", detail, re.I)
            c = m_c.group(1) if m_c else "?"
            ts = "%s:%s" % (m_t.group(1), m_t.group(2)) if m_t else "?"
            return "HS C:%s %s %s" % (c, ts, name)[:21]
        # Deauth messages: "Deauthing AA:BB:CC (5/10)" → "DEAUTH 5/10"
        if "DEAUTH" in phase:
            m = re.search(r"(\d+)\s*(?:/|of)\s*(\d+)", detail)
            if m:
                return "DEAUTH %s/%s %s" % (m.group(1), m.group(2), name)
            return "DEAUTH %s" % name
        # Cracking: "Running aircrack-ng with rockyou.txt (1/1)"
        if "CRACK" in phase:
            return "CRACK %s" % name
        # Generic phase: squeeze the identifier
        short = phase.replace("WPS ", "").replace("WPA ", "").replace("HANDSHAKE", "HS")
        return "%s %s %s" % (short[:10], name, detail[:8])

    @property
    def compact_detail(self) -> str:
        """Short detail line complementing compact (if needed)."""
        if self.type == EventType.PHASE:
            return (self.detail or "")[:21]
        if self.type == EventType.CRACKED:
            return ""
        if self.type == EventType.CLIENT:
            return ""
        return (self.detail or "")[:21]


def strip_ansi(value: Optional[str]) -> str:
    return ANSI.sub("", value or "")


def _fail_reason(text):
    """Compress a wifite failure message into a short, glanceable reason."""
    t = (text or "").lower()
    if "lock" in t:
        return "wps lock"
    if "timeout" in t or "timed out" in t:
        return "timeout"
    if "no client" in t or "clients:0" in t or "clients=0" in t:
        return "no client"
    if "no attacks succeeded" in t or "no attack" in t:
        return "no attack"
    if "assoc" in t:
        return "no assoc"
    return "failed"


class OutputParser:
    """Return typed events. Unknown lines remain visible as MESSAGE events."""

    def __init__(self):
        self.current_essid = None
        self.current_bssid = None
        self.current_signal = None

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
        values.setdefault("signal", self.current_signal)
        return AttackEvent(kind, raw=line, **values)

    # Attack types wifite passes as the first arg of Color.pattack().
    # Group 2 captures the target power (dBm) so we can surface signal strength.
    PATTACK = re.compile(
        r"\[\+\]\s+(.+?)\s+\(\s*([-+]?\d+|\?\?)\s*db\)\s+"
        r"(WPA3|WPA|WPS|WEP|PMKID|OWE|EVIL\s*TWIN)\s+(.+?):\s*(.*)$",
        re.I)

    def _parse(self, line: str) -> AttackEvent:
        # 1) The workhorse: every attack type emits a Color.pattack() line
        #    "[+] <essid> (<power>db) <TYPE> <name>: <progress>".
        m = self.PATTACK.match(line)
        if m:
            return self._parse_pattack(m, line)

        # 2) Scan summary (native scanner; rarely emitted, kept for completeness).
        match = re.search(
            r"Scanning.*?(?:Targets?|Found)[:\s]*(\d+).*?(?:Clients?)[:\s]*(\d+)",
            line, re.I)
        if match:
            return self._event(EventType.SCAN, line, targets=int(match.group(1)),
                               clients=int(match.group(2)))

        # 3) Target selection / attack start (no power field, so not a pattack).
        match = re.search(r"found target\s+(%s)\s+\((.*?)\)" % BSSID, line, re.I)
        if match:
            self.current_bssid, self.current_essid = match.group(1), match.group(2)
            return self._event(EventType.TARGET, line)

        match = re.search(
            r"\((\d+)/(\d+)\)\s+Starting attacks against\s+(%s)\s+\((.*?)\)"
            % BSSID, line, re.I)
        if match:
            self.current_bssid, self.current_essid = match.group(3), match.group(4)
            essid = None if match.group(4).lower().startswith("essid unknown") \
                else match.group(4)
            self.current_essid = essid
            return self._event(EventType.TARGET, line, essid=essid,
                               current=int(match.group(1)),
                               total=int(match.group(2)))

        # 4) Bare capture / crack lines (outside the pattack formatter).
        if re.search(r"Captured\s+PMKID", line, re.I):
            return self._event(EventType.PMKID, line)
        if re.search(r"Captured\s+handshake", line, re.I):
            return self._event(EventType.HANDSHAKE, line)

        match = re.search(r"Cracked WPS PIN:\s*(\S+)(?:\s+PSK:\s*(\S+))?",
                          line, re.I)
        if match:
            return self._event(EventType.CRACKED, line,
                               credential=match.group(2) or match.group(1))
        # Final-summary key lines: "Key: <psk>", "PSK (password): <psk>", "PIN: <pin>".
        match = re.search(r"^(?:\[[+C]\]\s*)?(?:PSK(?:\s*\(password\))?|Key):\s*(\S+)",
                          line, re.I)
        if match:
            return self._event(EventType.CRACKED, line, credential=match.group(1))

        # 5) Failures / skips / clients / deauth.
        if re.search(r"\[!\].*(?:FAILED|Failed:|Target timeout|No attacks succeeded)",
                     line, re.I):
            return self._event(EventType.FAILED, line, detail=_fail_reason(line))
        match = re.search(r"\[!\]\s+Skipping\s+.*?(?:attack on|on)\s+(.+?)(?:\s+because|$)",
                          line, re.I)
        if match:
            return self._event(EventType.SKIPPED, line, essid=match.group(1).strip())
        match = re.search(r"Discovered (?:new )?client:\s*(%s|\S+)" % BSSID, line, re.I)
        if match:
            return self._event(EventType.CLIENT, line, detail=match.group(1))
        match = re.search(r"sent\s+(\d+)\s+deauth", line, re.I)
        if match:
            return self._event(EventType.DEAUTH, line, current=int(match.group(1)))
        match = re.search(r"Deauth(?:ing)?[^0-9]*(\d+)\s*(?:/|of)\s*(\d+)", line, re.I)
        if match:
            return self._event(EventType.DEAUTH, line,
                               current=int(match.group(1)),
                               total=int(match.group(2)))
        return self._event(EventType.MESSAGE, line, detail=line[:48])

    def _parse_pattack(self, m, line: str) -> AttackEvent:
        essid = m.group(1).strip()
        power = m.group(2)
        atype = re.sub(r"\s+", " ", m.group(3).strip().upper())
        name = m.group(4).strip()
        prog = m.group(5).strip()
        if essid and essid.lower() not in ("unknown", "essid unknown"):
            self.current_essid = essid
        if power and power != "??":
            self.current_signal = int(power)
        # Associated-client count (WPA handshake needs one to deauth).
        mc = re.search(r"clients?[:=]\s*(\d+)", prog, re.I)
        clients = int(mc.group(1)) if mc else None
        phase = ("%s %s" % (atype, name)).upper()
        blob = "%s %s" % (name, prog)

        # Cracked: WPS ("Cracked WPS PIN: <pin> PSK: <psk>"), or PMKID/WPA/SAE
        # ("... CRACKED: Key: <psk>"), or any progress carrying "Key: <psk>".
        cracked = re.search(r"Cracked WPS PIN:\s*(\S+)(?:\s+PSK:\s*(\S+))?", prog, re.I)
        if cracked:
            return self._event(EventType.CRACKED, line,
                               credential=cracked.group(2) or cracked.group(1))
        if name.upper() == "CRACKED" or re.search(r"\bKey:\s*\S", prog):
            key = re.search(r"\bKey:\s*(\S+)", prog)
            return self._event(EventType.CRACKED, line,
                               credential=key.group(1) if key else None)

        # Capture confirmations arrive inside the progress text.
        if re.search(r"Captured\s+PMKID", blob, re.I):
            return self._event(EventType.PMKID, line)
        if re.search(r"Captured\s+handshake", blob, re.I):
            return self._event(EventType.HANDSHAKE, line)

        # Cracking in progress (PMKID name == "CRACK", or "Cracking ... wordlist").
        if name.upper() == "CRACK" or re.search(r"\bCracking\b", prog, re.I):
            return self._event(EventType.CRACKING, line, phase=phase, detail=prog)

        # Genuine failure (avoid matching the benign "timeout:MM:SS" countdown).
        if re.search(r"\bFAILED\b|Failed:|Target timeout", blob, re.I):
            return self._event(EventType.FAILED, line, detail=_fail_reason(blob))

        # Otherwise it's a live phase heartbeat (Listening/Waiting/Sending/etc).
        return self._event(EventType.PHASE, line, phase=phase, detail=prog,
                           clients=clients)
