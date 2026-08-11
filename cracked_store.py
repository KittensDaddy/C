# -*- coding: utf-8 -*-
"""Read/write cracked.json with corruption-tolerant fallbacks."""
import json
import os
import time
import shutil

import config


def load_cracked():
    """Return list of cracked entries. Self-heals corrupt files."""
    path = config.CRACKED_FILE
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, ValueError):
        # corrupt -> try to salvage by truncating at last ']'
        try:
            raw = open(path, "r").read()
            idx = raw.rfind("]")
            if idx != -1:
                salvaged = json.loads(raw[:idx + 1])
                if isinstance(salvaged, list):
                    return salvaged
        except Exception:  # noqa: BLE001
            pass
        # give up: back up and reset
        try:
            shutil.copy(path, path + ".bak")
        except Exception:  # noqa: BLE001
            pass
        save_cracked([])
        return []


def save_cracked(entries):
    """Atomically write entries to cracked.json."""
    path = config.CRACKED_FILE
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        try:
            os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass
        return False


def count_cracked():
    return len(load_cracked())


def new_since(timestamp):
    """Entries with date > timestamp."""
    return [e for e in load_cracked() if e.get("date", 0) > (timestamp or 0)]


# ---------------------------------------------------------------------------
# Upload state (incremental upload tracking)
# ---------------------------------------------------------------------------
UPLOAD_STATE_FILE = config.PROJECT_DIR + "/upload_state.json"


def load_upload_state():
    try:
        with open(UPLOAD_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_upload_state(state):
    tmp = UPLOAD_STATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, UPLOAD_STATE_FILE)
        return True
    except Exception:  # noqa: BLE001
        try:
            os.remove(tmp)
        except Exception:  # noqa: BLE001
            pass
        return False
