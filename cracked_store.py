# -*- coding: utf-8 -*-
"""Read/write runtime data with corruption-tolerant fallbacks.

Data lives in ~/.local/share/wifi-box/ (migrated from project root on first
run). Atomic temp-file replacement, dir mode 0700, cred files 0600.
"""
import json
import os
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
def load_cracked():
    _migrate()
    return _load_json(config.CRACKED_FILE)


def save_cracked(entries):
    _ensure_dir()
    return _atomic_write(config.CRACKED_FILE, entries, mode=0o600)


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
