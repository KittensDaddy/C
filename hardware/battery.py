# -*- coding: utf-8 -*-
"""INA219 battery monitor with graceful fallback when the sensor is absent."""
import time
import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from ui import theme  # noqa: E402


class Battery:
    def __init__(self):
        self.ina = None
        self.available = False
        self.voltage = None
        self.percent = None
        self._last_read = 0.0        # cache: I2C read is polled per frame
        self._last_vstr = "-.-V"
        self._init()

    def _init(self):
        for probe in config.INA219_ADDRS:
            try:
                import INA219
                ina = INA219.INA219(config.INA219_BUS, probe)
                # force a read to validate the bus responds
                v = ina.getBusVoltage_V()
                self.ina = ina
                self.available = True
                self.voltage = v
                break
            except Exception:  # noqa: BLE001
                continue
        if not self.available:
            self.voltage = None

    def read(self, max_age=2.0):
        """Update voltage/percent (cached). Returns (percent, voltage_str)."""
        now = time.time()
        if now - self._last_read < max_age:
            return self.percent, self._last_vstr
        self._last_read = now
        if self.ina is not None:
            try:
                v = self.ina.getBusVoltage_V()
                self.voltage = v
                p = (v - 3.0) / 1.2 * 100.0
                self.percent = max(0, min(100, p))
                self._last_vstr = "%.1fV" % v
                return self.percent, self._last_vstr
            except Exception:  # noqa: BLE001
                self.available = False
                self.ina = None
        self.percent = None
        self._last_vstr = "-.-V"
        return None, self._last_vstr

    def color(self):
        """Battery bar color based on percent: white/orange/red."""
        if self.percent is None:
            return theme.palette()["text"]
        if self.percent >= config.BATTERY_HIGH:
            return (255, 255, 255)     # white
        if self.percent >= config.BATTERY_MID:
            return (255, 160, 0)       # orange
        return (255, 0, 0)             # red

    def bar(self, draw, x, y, w, h):
        """Draw battery bar + terminal on the canvas."""
        from ui import theme as th
        if self.percent is None:
            th.progress_bar(draw, x, y, w, h, 0, color=(90, 90, 90))
            return
        th.progress_bar(draw, x, y, w, h, self.percent, color=self.color())


battery = Battery()
