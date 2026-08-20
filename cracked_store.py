# -*- coding: utf-8 -*-
"""Read/write runtime data with corruption-tolerant fallbacks.

Data lives in ~/.local/share/wifi-box/ (migrated from project root on first
run). Atomic temp-file replacement, dir mode 0700, cred files 0600.
"""
import json
import os
import re
import time
import shutil

import config


def _ensure_dir():
    try:
        os.makedirs(config.DATA_DIR, mode=0o700, exist_ok=True)
    except OSError:
        pass


def _migrate():
    """One-time: copy legacy files to DATA_DIR if they exist only in root."""
    _ensure_dir()
    for legacy, target in [(config.LEGACY_CRACKED, config.CRACKED_FILE),
                           (config.LEGACY_UPLOAD, config.UPLOAD_STATE_FILE)]:
        if os.path.exists(legacy) and not os.path.exists(target):
            try:
                shutil.copy2(legacy, target)
            except OSError:
                pass


def _atomic_write(path, data, mode=0o600):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _load_json(path):
    """Return parsed JSON or empty list/dict on failure with self-heal."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        try:
            raw = open(path, "r").read()
            idx = raw.rfind("]")
            if idx != -1:
                data = json.loads(raw[:idx + 1])
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        try:
            shutil.copy(path, path + ".bak")
        except OSError:
            pass
        _atomic_write(path, [])
        return []


# ---------------------------------------------------------------------------
# cracked.json
# ---------------------------------------------------------------------------
def _looks_like_mac(s):
    s = (s or "").strip()
    if not s:
        return False
    return bool(re.match(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$", s) or
                re.match(r"^[0-9A-Fa-f]{12}$", s))


def _pick_essid(a, b):
    """Prefer a real network name over a bare MAC / empty string."""
    for cand in (a, b):
        if cand and not _looks_like_mac(cand):
            return cand
    return a or b


def _pick_cap(a, b):
    for cand in (a, b):
        if cand and os.path.exists(cand):
            return cand
    return a or b


def _merge_two(keep, other):
    """Fold `other` into `keep` (same normalized BSSID). Mutates keep."""
    keep["essid"] = _pick_essid(keep.get("essid"), other.get("essid"))
    for key in ("psk", "pin", "channel", "type"):
        if other.get(key) and not keep.get(key):
            keep[key] = other[key]
    # Prefer a PSK over a PIN-only row when both exist across duplicates.
    if other.get("psk") and not keep.get("psk"):
        keep["psk"] = other["psk"]
    keep["cap"] = _pick_cap(keep.get("cap"), other.get("cap"))
    keep["date"] = max(int(keep.get("date") or 0), int(other.get("date") or 0))
    # Canonicalize MAC spelling once.
    nb = config._norm_bssid(keep.get("bssid") or other.get("bssid"))
    if nb:
        keep["bssid"] = nb
    return keep


def merge_by_bssid(entries):
    """Collapse duplicate cracked rows that share the same MAC.

    Entries with no usable BSSID are left untouched (kept in original order
    after the merged-MAC rows). Returns (merged_list, changed).
    """
    if not entries:
        return [], False
    order = []          # first-seen normalized BSSID
    by_mac = {}         # norm -> entry
    no_mac = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        nb = config._norm_bssid(e.get("bssid"))
        if not nb or len(re.sub(r"[^0-9A-Fa-f]", "", nb)) != 12:
            no_mac.append(dict(e))
            continue
        if nb not in by_mac:
            row = dict(e)
            row["bssid"] = nb
            by_mac[nb] = row
            order.append(nb)
        else:
            _merge_two(by_mac[nb], e)
    merged = [by_mac[k] for k in order] + no_mac
    changed = len(merged) != len(entries)
    if not changed:
        # Also rewrite if any BSSID spelling was non-canonical.
        for old, new in zip(entries, merged):
            if not isinstance(old, dict):
                changed = True
                break
            if config._norm_bssid(old.get("bssid")) != new.get("bssid"):
                changed = True
                break
            if old.get("essid") != new.get("essid") or old.get("psk") != new.get("psk"):
                # Content may have been folded even when counts match? counts
                # wouldn't match if we folded. Skip.
                pass
    return merged, changed


def load_cracked():
    """Load cracked.json, merging same-MAC duplicates (and rewrite if needed)."""
    _migrate()
    entries = _load_json(config.CRACKED_FILE)
    merged, changed = merge_by_bssid(entries)
    if changed:
        save_cracked(merged)
    return merged


def save_cracked(entries):
    _ensure_dir()
    merged, _ = merge_by_bssid(entries if isinstance(entries, list) else [])
    return _atomic_write(config.CRACKED_FILE, merged, mode=0o600)


def update_cracked(bssid, **fields):
    """Update an existing cracked entry by BSSID (e.g. backfill a PSK from a
    PIN). Fields are only written when truthy. Returns True if anything changed."""
    want = config._norm_bssid(bssid)
    entries = load_cracked()
    changed = False
    for e in entries:
        if config._norm_bssid(e.get("bssid")) == want:
            for k, v in fields.items():
                if v and not e.get(k):
                    e[k] = v
                    changed = True
            break
    if changed:
        save_cracked(entries)
    return changed


def count_cracked():
    return len(load_cracked())


def new_since(timestamp):
    return [e for e in load_cracked() if e.get("date", 0) > (timestamp or 0)]


# ---------------------------------------------------------------------------
# upload_state.json — incremental-upload bookmark
# ---------------------------------------------------------------------------
def load_upload_state():
    _migrate()
    path = config.UPLOAD_STATE_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_upload_state(state):
    _ensure_dir()
    return _atomic_write(config.UPLOAD_STATE_FILE, state, mode=0o600)


# ---------------------------------------------------------------------------
# settings.json — theme, presets, exclusions (survives reboot)
# ---------------------------------------------------------------------------
def load_settings():
    path = config.SETTINGS_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def save_settings(data):
    _ensure_dir()
    return _atomic_write(config.SETTINGS_FILE, data, mode=0o600)
