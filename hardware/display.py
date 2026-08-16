# -*- coding: utf-8 -*-
"""LCD wrapper with two backends:

  * fbtft framebuffer (/dev/fb1) — the kernel shows the boot log on the LCD, and
    we draw the UI on top by writing RGB565 to the framebuffer. Preferred, so the
    boot log replaces the old white screen.
  * legacy spidev driver (LCD_1in44) — fallback when no fbtft framebuffer exists.

Runs headless (logged) if neither is available. The public API (begin/show/
overlay/canvas) is unchanged so the rest of the UI doesn't care which backend.
"""
import sys
import os
import time
import threading
import glob

# Make project root importable (so LCD_1in44 / config resolve) regardless of CWD
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import config  # noqa: E402
from ui.theme import palette  # noqa: E402

try:
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None


def _to_fb565(canvas):
    """PIL RGB image -> RGB565 little-endian bytes (Linux framebuffer order)."""
    rgb = canvas.convert("RGB")
    if _np is not None:
        a = _np.asarray(rgb, dtype=_np.uint16)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        val = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        return val.astype("<u2").tobytes()
    raw = rgb.tobytes()
    out = bytearray((len(raw) // 3) * 2)
    j = 0
    for i in range(0, len(raw), 3):
        v = ((raw[i] & 0xF8) << 8) | ((raw[i + 1] & 0xFC) << 3) | (raw[i + 2] >> 3)
        out[j] = v & 0xFF
        out[j + 1] = (v >> 8) & 0xFF
        j += 2
    return bytes(out)


class Display:
    """Thin, failure-tolerant wrapper around the LCD (framebuffer or spidev)."""

    def __init__(self):
        self.width = config.WIDTH
        self.height = config.HEIGHT
        self.available = False
        self._canvas = None
        self._draw = None
        self._lock = threading.RLock()
        self._frame_open = False
        self._fb = None          # framebuffer file handle (fbtft mode)
        self.disp = None         # spidev LCD object (fallback mode)
        # Real framebuffer geometry (the fbtft device is often 128x160 or has a
        # stride wider than the panel, leaving unwritten edge pixels as garbage).
        self._fb_w = config.WIDTH
        self._fb_h = config.HEIGHT
        self._fb_stride = config.WIDTH * 2
        self._init_hw()

    # -- backend selection -------------------------------------------------
    def _find_fbtft(self):
        """Return /dev/fbN for an fbtft (SPI) framebuffer, or None."""
        for path in sorted(glob.glob("/sys/class/graphics/fb*/name")):
            try:
                name = open(path).read().strip().lower()
            except OSError:
                continue
            if "st7735" in name or name.startswith("fb_"):
                num = path.split("/graphics/fb")[1].split("/")[0]
                return "/dev/fb%s" % num
        return None

    def _init_hw(self):
        fbdev = self._find_fbtft()
        if fbdev:
            try:
                self._fb = open(fbdev, "r+b", buffering=0)
                self._read_fb_geometry(fbdev)
                self.available = True
                self._quiet_console()
                self._log("LCD via framebuffer %s (fbtft) %dx%d stride=%d"
                          % (fbdev, self._fb_w, self._fb_h, self._fb_stride))
                return
            except Exception as e:  # noqa: BLE001
                self._log("framebuffer open failed: %s" % e)
                self._fb = None
        # Fallback: legacy spidev driver
        try:
            import LCD_1in44
            self.disp = LCD_1in44.LCD()
            self.disp.LCD_Init(LCD_1in44.SCAN_DIR_DFT)
            self.disp.LCD_Clear()
            self.available = True
            self._log("LCD via spidev driver")
        except Exception as e:  # noqa: BLE001
            self.disp = None
            self._log("no LCD (%s); running headless" % e)

    def _read_fb_geometry(self, fbdev):
        """Read the framebuffer's real resolution + stride so the 128x128 canvas
        is blitted into the correct location and leftover pixels are zeroed."""
        num = fbdev.rstrip("/").rsplit("fb", 1)[-1]
        base = "/sys/class/graphics/fb%s" % num
        try:
            vs = open(base + "/virtual_size").read().strip()
            w, h = (int(x) for x in vs.split(","))
            self._fb_w, self._fb_h = w, h
        except Exception:  # noqa: BLE001
            pass
        try:
            self._fb_stride = int(open(base + "/stride").read().strip())
        except Exception:  # noqa: BLE001
            try:
                bpp = int(open(base + "/bits_per_pixel").read().strip())
                self._fb_stride = self._fb_w * (bpp // 8)
            except Exception:  # noqa: BLE001
                self._fb_stride = self._fb_w * 2

    def _quiet_console(self):
        """Stop the fbcon cursor blinking over the UI once we own the fb."""
        try:
            with open("/sys/class/graphics/fbcon/cursor_blink", "w") as f:
                f.write("0")
        except OSError:
            pass

    def _log(self, msg):
        try:
            with open(config.LOG_FILE, "a") as f:
                f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:  # noqa: BLE001
            pass

    # -- canvas helpers ----------------------------------------------------
    def begin(self, bg=None):
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

    def _push_full(self):
        if self._canvas is None:
            return
        if self._fb is not None:
            try:
                self._fb.seek(0)
                self._fb.write(self._fb565_frame(self._canvas))
            except Exception:  # noqa: BLE001
                pass
        elif self.disp is not None:
            try:
                self.disp.LCD_ShowImage(self._canvas, 0, 0)
            except Exception:  # noqa: BLE001
                pass

    def _fb565_frame(self, canvas):
        """RGB565 for the WHOLE framebuffer (not just the 128x128 canvas) so the
        panel's right/bottom padding is written as black instead of stale noise."""
        rgb = _to_fb565(canvas)   # W*H*2 bytes of the visible canvas
        row = self.width * 2
        if self._fb_w == self.width and self._fb_h == self.height \
                and self._fb_stride == row:
            return rgb
        # Build a full-size frame: blit the canvas rows at their stride, zero the
        # rest (offscreen columns/rows the panel still scans out).
        out = bytearray(self._fb_stride * self._fb_h)
        for y in range(min(self.height, self._fb_h)):
            off = y * self._fb_stride
            out[off:off + row] = rgb[y * row:(y + 1) * row]
        return bytes(out)

    def show(self):
        """Commit the current canvas and release the frame lock."""
        try:
            self._push_full()
        finally:
            if self._frame_open:
                self._frame_open = False
                self._lock.release()

    def overlay(self, paint_fn, region=None):
        """Repaint part of the canvas (status bar/cat) and commit without a new
        frame. In fb mode the whole canvas is written (cheap memory copy; fbtft
        pushes the panel on its own timer); in spidev mode only `region` rows are
        pushed to cut SPI cost."""
        from PIL import ImageDraw
        if not self._lock.acquire(timeout=0.05):
            return
        try:
            if self._canvas is None:
                return
            paint_fn(ImageDraw.Draw(self._canvas))
            if self._fb is not None:
                try:
                    self._fb.seek(0)
                    self._fb.write(self._fb565_frame(self._canvas))
                except Exception:  # noqa: BLE001
                    pass
            elif self.disp is not None:
                try:
                    if region is not None and hasattr(self.disp,
                                                      "LCD_ShowImageWindow"):
                        self.disp.LCD_ShowImageWindow(self._canvas, region[0],
                                                      region[1])
                    else:
                        self.disp.LCD_ShowImage(self._canvas, 0, 0)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            self._lock.release()

    def clear(self):
        if self._fb is not None:
            try:
                self._fb.seek(0)
                self._fb.write(b"\x00" * (self._fb_stride * self._fb_h))
            except Exception:  # noqa: BLE001
                pass
        elif self.disp is not None:
            try:
                self.disp.LCD_Clear()
            except Exception:  # noqa: BLE001
                pass


# Module-level singleton
display = Display()
