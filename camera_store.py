# -*- coding: utf-8 -*-
"""Persist discovered cameras + recovered access to cameras.json.

Mirrors cracked_store.py: corrupt-tolerant JSON, atomic temp-file replace,
cred files 0600, lives in ~/.local/share/wifi-box/.
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


def load_cameras():
    _ensure_dir()
    return _load_json(config.CAMERAS_FILE)


def save_cameras(entries):
    _ensure_dir()
    return _atomic_write(config.CAMERAS_FILE, entries, mode=0o600)


def upsert_camera(ip, **fields):
    """Insert or merge a camera entry keyed by IP. Truthy fields only."""
    entries = load_cameras()
    for e in entries:
        if e.get("ip") == ip:
            for k, v in fields.items():
                if v:
                    e[k] = v
            save_cameras(entries)
            return e
    entry = {"ip": ip, "date": int(time.time())}
    entry.update({k: v for k, v in fields.items() if v})
    entries.append(entry)
    save_cameras(entries)
    return entry
