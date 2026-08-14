# -*- coding: utf-8 -*-
"""Typed contracts for native attacks: requests, results, live events.

No wifite2, no stdout scraping. Engine modules emit AttackEvent / status dicts
directly at each orchestration step; the UI (ui/screens.py AttackStatus) consumes
the same status_cb dict shapes it always has.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


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


@dataclass(frozen=True)
class AttackRequest:
    """What the user asked for — a frozen snapshot, never reads stale globals."""
    interface: str
    preset: Optional[Dict[str, Any]] = None
    # Multi-target selection (native engine): BSSIDs to include / exclude.
    target_bssids: List[str] = field(default_factory=list)
    exclude_bssids: List[str] = field(default_factory=list)
    exclude_essids: List[str] = field(default_factory=list)
    bands: str = "both"            # "2" | "5" | "both"
    attacks: Set[str] = field(default_factory=lambda: {"wpa", "wps"})
    # Frozen config snapshots
    attack_modes: List[Dict[str, Any]] = field(default_factory=list)
    timing: List[Dict[str, Any]] = field(default_factory=list)
    filters: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AttackResult:
    """What happened — never mutates after creation."""
    ok: bool
    error: Optional[str] = None
    cancelled: bool = False
    cracked: List[Dict[str, Any]] = field(default_factory=list)
    handshakes: List[Dict[str, Any]] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    command: Optional[str] = None
    monitor_iface: Optional[str] = None
    elapsed: float = 0.0


@dataclass(frozen=True)
class AttackEvent:
    """One live update. `.as_dict()` is what gets handed to status_cb."""
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
    countdown: Optional[int] = None    # seconds remaining in the current phase
    cd_max: Optional[int] = None       # phase length (for the depletion gauge)
    pin: Optional[str] = None          # WPS PIN (distinct from the WPA PSK)

    def as_dict(self) -> Dict[str, Any]:
        value = {k: v for k, v in self.__dict__.items() if v is not None}
        value["type"] = self.type.value
        if self.credential is not None:
            value["psk"] = self.credential
            value.pop("credential", None)
        return value
