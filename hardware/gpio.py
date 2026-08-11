# -*- coding: utf-8 -*-
"""Single lgpio chip handle shared by buttons and LCD. Prevents gpiozero/lgpio
chip conflict on Pi 5 (both use /dev/gpiochipN under the hood)."""

import time as _time

_lgpio = None
_chip = None
_available = False


def _open():
    global _lgpio, _chip, _available
    try:
        import lgpio as _lg
    except ImportError:
        return
    for n in (4, 0, 1):
        try:
            _chip = _lg.gpiochip_open(n)
            _lgpio = _lg
            _available = True
            return
        except Exception:
            continue


def init():
    if not _available:
        _open()
    return _available


def available():
    return _available


def claim_input(pin, pull_up=True):
    """Claim a pin as input. Returns True on success."""
    if not _available:
        return False
    try:
        pud = _lgpio.SET_PULL_UP if pull_up else _lgpio.SET_PULL_DOWN
        _lgpio.gpio_claim_input(_chip, pin, pud)
        return True
    except Exception:
        return False


def claim_output(pin, initial=0):
    """Claim a pin as output. Returns True on success."""
    if not _available:
        return False
    try:
        _lgpio.gpio_claim_output(_chip, pin, initial=initial)
        return True
    except Exception:
        return False


def write(pin, value):
    if not _available:
        return
    try:
        _lgpio.gpio_write(_chip, pin, value)
    except Exception:
        pass


def read(pin):
    if not _available:
        return 0
    try:
        return _lgpio.gpio_read(_chip, pin)
    except Exception:
        return 0


def close():
    global _available
    if _available and _chip is not None:
        try:
            _lgpio.gpiochip_close(_chip)
        except Exception:
            pass
    _available = False


def delay_ms(ms):
    _time.sleep(ms / 1000.0)


class Pin:
    """Minimal gpiozero-compatible wrapper for LCD driver compatibility."""

    def __init__(self, pin, mode_output=True, initial=False):
        self._pin = pin
        if mode_output:
            claim_output(pin, 1 if initial else 0)
        else:
            claim_input(pin)

    def on(self):
        write(self._pin, 1)

    def off(self):
        write(self._pin, 0)

    @property
    def value(self):
        return read(self._pin)

    @value.setter
    def value(self, v):
        write(self._pin, 1 if v else 0)

    def close(self):
        pass

    # Dummy PWM support for backlight (just on/off)
    @property
    def frequency(self):
        return 1000

    @frequency.setter
    def frequency(self, v):
        pass
