# -*- coding: utf-8 -*-
"""Button handling via lgpio, with graceful fallback when GPIO is unavailable.

Provides:
  Button poll / hold detection (debounce + accelerated scrolling)
  Event dispatch to the active UI callback.
"""
import time
import threading

import config


def _open_chip():
    """Try to open a gpiochip, falling back across chip numbers."""
    try:
        import lgpio
    except ImportError:
        return None, None

    # lgpio.gpiochip_open() takes a chip number (0, 1, 4 for Pi5, ...).
    for n in (4, 0, 1):
        try:
            return lgpio, lgpio.gpiochip_open(n)
        except Exception:  # noqa: BLE001
            continue
    return None, None


class Button:
    """One debounced, hold-aware button."""

    def __init__(self, lgpio, chip, pin, pull_up=True):
        self._lg = lgpio
        self._chip = chip
        self.pin = pin
        self._last_state = False   # True = pressed (active low)
        self._last_change = 0.0
        self._pressed_since = None
        try:
            pud = lgpio.SET_PULL_UP if pull_up else lgpio.SET_PULL_DOWN
            lgpio.gpio_claim_input(chip, pin, pud)
        except Exception:  # noqa: BLE001
            pass

    def read(self):
        """True if the button is currently pressed."""
        try:
            return self._lg.gpio_read(self._chip, self.pin) == 0  # active low
        except Exception:  # noqa: BLE001
            return False

    def update(self, debounce=config.DEBOUNCE_TIME):
        """Return ('press'|'hold'|None) transition for this tick."""
        now = time.time()
        pressed = self.read()
        if not pressed:
            self._pressed_since = None
            return None
        if self._pressed_since is None:
            self._pressed_since = now
            self._last_change = now
            return "press"
        if now - self._last_change >= debounce and \
           now - self._pressed_since >= debounce:
            self._last_change = now
            return "hold"
        return None


class ButtonManager:
    """Owns all buttons and dispatches events to a callback.

    callback(event) where event is a dict:
        {"type": "up"|"down"|"left"|"right"|"press"|"key1"|"key2"|"key3",
         "hold": bool}
    """

    def __init__(self, callback=None):
        self.callback = callback
        self.available = False
        self.buttons = {}
        self._running = False
        self._thread = None
        self._init()

    def _init(self):
        lgpio, chip = _open_chip()
        if lgpio is None:
            return
        self.available = True
        # (name, pin, pull_up)
        pins = {
            "up":    (config.KEY_UP_PIN,    True),
            "down":  (config.KEY_DOWN_PIN,  True),
            "left":  (config.KEY_LEFT_PIN,  True),
            "right": (config.KEY_RIGHT_PIN, True),
            "press": (config.KEY_PRESS_PIN, True),
            "key1":  (config.KEY1_PIN,      True),
            "key2":  (config.KEY2_PIN,      True),
            "key3":  (config.KEY3_PIN,      True),
        }
        for name, (pin, pud) in pins.items():
            self.buttons[name] = Button(lgpio, chip, pin, pud)
        self._lg = lgpio
        self._chip = chip

    def start(self):
        if not self.available or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            for name, btn in self.buttons.items():
                ev = btn.update()
                if ev:
                    self._emit(name, hold=(ev == "hold"))
            time.sleep(0.02)

    def _emit(self, name, hold):
        if self.callback:
            self.callback({"type": name, "hold": hold})

    def stop(self):
        self._running = False
        try:
            if getattr(self, "_lg", None) is not None:
                self._lg.gpiochip_close(self._chip)
        except Exception:  # noqa: BLE001
            pass
