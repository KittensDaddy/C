# -*- coding: utf-8 -*-
"""LCD wrapper. Initializes the Waveshare 1.44" SPI display and exposes a
small drawing/rendering API. If the display cannot initialize we keep running
headless (attacks still work, status is logged).
"""
import sys
import os
import time
import threading

# Make project root importable (so LCD_1in44 / config resolve) regardless of CWD
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from ui.theme import palette  # noqa: E402  (light draw helpers)


class Display:
    """Thin, failure-tolerant wrapper around the LCD."""

    def __init__(self):
        self.disp = None
        self.available = False
        self.width = config.WIDTH
        self.height = config.HEIGHT
        self._canvas = None
        self._draw = None
        # Serialize full-frame renders with the background animator overlay.
        self._lock = threading.RLock()
        self._frame_open = False
        self._init_hw()

    def _init_hw(self):
        for mod in ("LCD_1in44",):
            try:
                __import__(mod)
                break
            except Exception:
                pass
        else:
            self._log("LCD driver module missing; running headless")
            return
        try:
            lcd = sys.modules["LCD_1in44"]
            self.disp = lcd.LCD()
            self.disp.LCD_Init(lcd.SCAN_DIR_DFT)
            self.disp.LCD_Clear()
            self.available = True
        except Exception as e:  # noqa: BLE001
            self.disp = None
            self.available = False
            self._log("LCD init failed: %s (running headless)" % e)

    def _log(self, msg):
        try:
            with open(config.LOG_FILE, "a") as f:
                f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    # -- canvas helpers ----------------------------------------------------
    def begin(self, bg=None):
        """Start a frame: return the PIL ImageDraw instance.

        Acquires the render lock (released by show()) so the background
        animator can't overlay mid-draw."""
        from PIL import Image, ImageDraw
        self._lock.acquire()
        self._frame_open = True
        bg = bg or palette()["background"]
        self._canvas = Image.new("RGB", (self.width, self.height), bg)
        self._draw = ImageDraw.Draw(self._canvas)
        return self._draw

    @property
    def draw(self):
        return self._draw

    @property
    def canvas(self):
        return self._canvas

    def show(self):
        """Commit the current canvas to the screen and release the frame lock."""
        try:
            if self.disp is not None and self._canvas is not None:
                self.disp.LCD_ShowImage(self._canvas, 0, 0)
        except Exception:  # noqa: BLE001
            pass
        finally:
            if self._frame_open:
                self._frame_open = False
                self._lock.release()

    def overlay(self, paint_fn):
        """Repaint part of the current canvas (e.g. the status bar) and commit,
        without starting a new frame. Used by the animation ticker. Skips if a
        full-frame render is in progress."""
        from PIL import ImageDraw
        if not self._lock.acquire(timeout=0.05):
            return
        try:
            if self._canvas is not None:
                paint_fn(ImageDraw.Draw(self._canvas))
                if self.disp is not None:
                    try:
                        self.disp.LCD_ShowImage(self._canvas, 0, 0)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            self._lock.release()

    def compose(self, fn):
        """Let fn(canvas_image) mutate the current canvas, then commit. Used by
        the full-screen screensaver. Skips if a full-frame render is running."""
        if not self._lock.acquire(timeout=0.05):
            return
        try:
            if self._canvas is not None:
                fn(self._canvas)
                if self.disp is not None:
                    try:
                        self.disp.LCD_ShowImage(self._canvas, 0, 0)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            self._lock.release()

    def clear(self):
        if self.disp is not None:
            try:
                self.disp.LCD_Clear()
            except Exception:  # noqa: BLE001
                pass


# Module-level singleton
display = Display()
