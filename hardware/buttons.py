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

    def __init__(self, pin):
        self.pin = pin
        self._last_change = 0.0
        self._pressed_since = None
        gpio.claim_input(pin, pull_up=True)

    def read(self):
        return gpio.read(self.pin) == 0  # active low

    def update(self, debounce=config.DEBOUNCE_TIME):
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
        for name, pin in pins.items():
            self.buttons[name] = Button(pin)

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
