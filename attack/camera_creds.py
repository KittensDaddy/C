# -*- coding: utf-8 -*-
"""Brand-aware default/weak credential table for IP cameras.

Keyed by platform (fingerprint), not model, so one entry covers the whole
Xiongmai/Herospeed family (V380/Yoosee/V360/FNKvision/Mdudu/KTvision/etc.) that
dominates the Shopee Thailand catalog. `load_creds()` merges a user drop-in file
(~/.local/share/wifi-box/camera_creds.txt) so the list is extensible on-device.
"""
import os

import config

# platform -> ordered list of (username, password). Order matters: most-likely
# defaults first. Tried via ONVIF digest first (fastest confirmation), then
# HTTP digest/basic, then RTSP digest.
DEFAULT_CREDS = {
    "xiongmai": [
        ("admin", ""),
        ("admin", "admin"),
        ("admin", "123456"),
        ("admin", "888888"),
        ("admin", "12345"),
        ("admin", "1111"),
        ("admin", "666666"),
        ("admin", "88888888"),
        ("admin", "12345678"),
        ("user", "user"),
        ("operator", "operator"),
    ],
    "hikvision": [
        ("admin", "12345"),
        ("admin", "123456789abc"),
        ("admin", "Hik12345"),
        ("admin", "1234567890"),
        ("admin", "7ujMko0admin"),
        ("admin", ""),
    ],
    "dahua": [
        ("admin", "admin"),
        ("admin", "123456"),
        ("888888", "888888"),
        ("admin", "666666"),
        ("admin", "888888"),
        ("666666", "666666"),
    ],
    "vstarcam": [
        ("admin", "888888"),
        ("admin", "123456"),
        ("admin", ""),
    ],
    "tenda": [
        ("admin", "admin"),
        ("admin", ""),
    ],
    "tapo": [
        ("admin", "admin"),
        ("admin", ""),
    ],
    "xiaomi": [
        ("admin", "admin"),
        ("admin", ""),
    ],
    # Generic fallback used when fingerprinting is inconclusive — the cheap
    # no-name cams overwhelmingly run Xiongmai, so mirror that list plus the
    # most common cross-brand defaults.
    "generic": [
        ("admin", ""),
        ("admin", "admin"),
        ("admin", "123456"),
        ("admin", "888888"),
        ("admin", "12345"),
        ("admin", "1111"),
        ("admin", "666666"),
        ("admin", "88888888"),
        ("user", "user"),
        ("operator", "operator"),
        ("root", "root"),
        ("admin", "password"),
    ],
}


def _parse_dropin(path):
    """Parse camera_creds.txt into {platform: [(user, pass), ...]}. Lines are
    `user:pass` (→ generic) or `platform user:pass`."""
    out = {}
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) == 2 and ":" in parts[1] \
                        and not ":" in parts[0].split(":")[0]:
                    platform, cred = parts
                else:
                    platform, cred = "generic", line
                if ":" not in cred:
                    continue
                user, _, pw = cred.partition(":")
                out.setdefault(platform, []).append((user, pw))
    except OSError:
        pass
    return out


def load_creds(platform=None):
    """Return the ordered (user, pass) list for a platform. `platform` is a
    key in DEFAULT_CREDS or None for a merged generic list."""
    base = {}
    for k, v in DEFAULT_CREDS.items():
        base.setdefault(k, []).extend(v)
    dropin = _parse_dropin(config.CAMERA_CREDS_FILE)
    for k, v in dropin.items():
        base.setdefault(k, []).extend(v)
    if platform in base:
        return base[platform]
    merged = []
    for key in ("generic", "xiongmai"):
        merged.extend(base.get(key, []))
    return merged


def creds_for(platform):
    """Resolve a fingerprint platform to its credential list, falling back to
    the generic/xiongmai merge when unknown or inconclusive."""
    if platform and platform in DEFAULT_CREDS:
        return DEFAULT_CREDS[platform]
    return DEFAULT_CREDS["generic"]
