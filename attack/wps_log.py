# -*- coding: utf-8 -*-
"""Experiment-grade WPS logging for method bake-offs.

Grep after a Rush:
  grep -aE 'wps-method|wps-ap|run-metric' ~/C/wifibox.log
"""
import time
import uuid

from attack import tools


def new_run_id():
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _fmt(fields):
    parts = []
    for k in sorted(fields.keys()):
        v = fields[k]
        if v is None or v == "":
            continue
        if isinstance(v, float):
            parts.append("%s=%.2f" % (k, v))
        elif isinstance(v, bool):
            parts.append("%s=%d" % (k, 1 if v else 0))
        else:
            s = str(v).replace(" ", "_")[:64]
            parts.append("%s=%s" % (k, s))
    return " ".join(parts)


def method(fields):
    """One attempt (reaver / oneshot / vendor pin / getpsk)."""
    tools.log("wps-method " + _fmt(fields))


def ap(fields):
    """Per-AP summary after all methods."""
    tools.log("wps-ap " + _fmt(fields))


def metric(fields):
    """Legacy timing line (kept for older greps)."""
    tools.log("wps-metric " + _fmt(fields))
