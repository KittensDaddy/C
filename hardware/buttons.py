# -*- coding: utf-8 -*-
"""Button handling via shared lgpio GPIO (hardware/gpio.py).

Poll / hold detection (debounce + accelerated scrolling), event dispatch.
"""
import time
import threading

import config
from hardware import gpio


class Button:
    """One debounced, hold-aware button."""

    def __init__(self, pin, repeat=True):
        self.pin = pin
        self.repeat = repeat          # action keys (press/keyN) fire once only
        self._last_change = 0.0
        self._pressed_since = None
        self._released_at = 0.0
        gpio.claim_input(pin, pull_up=True)

    def read(self):
        return gpio.read(self.pin) == 0  # active low

    def update(self, delay=None, rate=None, debounce=config.DEBOUNCE_TIME):
        delay = config.REPEAT_DELAY if delay is None else delay
        rate = config.REPEAT_RATE if rate is None else rate
        now = time.time()
        pressed = self.read()
        if not pressed:
            if self._pressed_since is not None:
                self._released_at = now
            self._pressed_since = None
            return None
        # New press: ignore contact bounce right after a release.
        if self._pressed_since is None:
            if now - self._released_at < debounce:
                return None
            self._pressed_since = now
            self._last_change = now
            return "press"
        # Held: auto-repeat only for nav keys, after an initial delay, slowly.
        if not self.repeat:
            return None
        if now - self._pressed_since >= delay and now - self._last_change >= rate:
            self._last_change = now
            return "hold"
        return None


class ButtonManager:
    """Owns all buttons and dispatches events to a callback.

    callback(event) -> {"type": "up"|"down"|...|"key3", "hold": bool}
    """

    def __init__(self, callback=None):
        self.callback = callback
        self.available = gpio.init()
        self.buttons = {}
        self._running = False
        self._thread = None
        self._init()

    def _init(self):
        if not self.available:
            return
        pins = {
            "up":    config.KEY_UP_PIN,
            "down":  config.KEY_DOWN_PIN,
            "left":  config.KEY_LEFT_PIN,
            "right": config.KEY_RIGHT_PIN,
            "press": config.KEY_PRESS_PIN,
            "key1":  config.KEY1_PIN,
            "key2":  config.KEY2_PIN,
            "key3":  config.KEY3_PIN,
        }
        # Only directional keys auto-repeat; action keys fire once per press.
        repeatable = {"up", "down", "left", "right"}
        for name, pin in pins.items():
            self.buttons[name] = Button(pin, repeat=(name in repeatable))

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
        gpio.close()
