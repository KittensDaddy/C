# -*- coding: utf-8 -*-
"""A tiny animated cat that gallops to the right along the status bar.

Drawn vector-style (body/head/ears/tail as primitives, legs as swinging lines)
so the gait stays smooth at any phase. Monochrome to match the UI palette.
"""
import math

# Nominal sprite box (the cat is drawn within W x H from its top-left origin).
W = 20
H = 11


def _leg(d, hipx, hipy, phase, leg_phase, color, reach=3.0, drop=3):
    """One galloping leg: hip fixed, foot swings fore/aft on a sine."""
    fx = hipx + reach * math.sin(2 * math.pi * phase + leg_phase)
    fy = hipy + drop - 1.2 * abs(math.cos(2 * math.pi * phase + leg_phase))
    d.line((hipx, hipy, fx, fy), fill=color, width=1)


def sprite(phase, color=(240, 240, 240), flip=False, scale=1):
    """Render the cat onto a transparent RGBA image (for free pasting/bouncing).
    flip mirrors it to face left; scale enlarges (nearest-neighbour)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (W + 2, H + 3), (0, 0, 0, 0))
    draw(ImageDraw.Draw(img), 0, 2, phase, color=color, eye=(30, 30, 30))
    if flip:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    if scale != 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    return img


def draw(d, x, y, phase, color=(240, 240, 240), eye=(20, 20, 20)):
    """Draw the cat with its top-left at (x, y); phase in [0,1) drives the gait."""
    def P(px, py):
        return (x + px, y + py)

    bob = 0.6 * math.sin(2 * math.pi * phase)   # tiny body bounce
    yb = y + bob

    # --- back legs (drawn first, behind the body) ---
    _leg(d, x + 6, yb + 8, phase, math.pi, color)          # back-far
    _leg(d, x + 8, yb + 8, phase, math.pi * 1.5, color)    # back-near

    # --- tail: sweeps up-left, sways with the gait ---
    sway = 1.6 * math.sin(2 * math.pi * phase + math.pi)
    d.line([P(4, 5 + bob), P(2, 3 + bob + sway * 0.4),
            P(0, 1 + bob + sway)], fill=color, joint="curve", width=1)

    # --- body ---
    d.ellipse((x + 3, yb + 3, x + 15, yb + 9), fill=color)
    # neck/shoulder blend
    d.polygon([P(11, 3 + bob), P(16, 3 + bob), P(15, 8 + bob), P(11, 8 + bob)],
              fill=color)

    # --- head + ears ---
    d.ellipse((x + 13, yb + 1, x + 19, yb + 7), fill=color)
    d.polygon([P(13, 2 + bob), P(14, -1 + bob), P(16, 2 + bob)], fill=color)  # ear
    d.polygon([P(16, 2 + bob), P(18, -1 + bob), P(19, 2 + bob)], fill=color)  # ear
    d.point(P(17, 4 + bob), fill=eye)                       # eye

    # --- front legs (in front of the body) ---
    _leg(d, x + 12, yb + 8, phase, 0.0, color)             # front-near
    _leg(d, x + 14, yb + 8, phase, math.pi * 0.5, color)   # front-far
