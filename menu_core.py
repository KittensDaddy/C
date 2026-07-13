from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
import time

import RPi.GPIO as GPIO

import display
import setting


ActionResult = str | None


@dataclass
class MenuItem:
    label: str | Callable[[], str]
    action: Callable[[], ActionResult] | None = None

    def text(self) -> str:
        return self.label() if callable(self.label) else self.label


def _wait_until_visible() -> None:
    while display.is_stealth_mode_active():
        display.exit_stealth_mode()
        time.sleep(0.1)


def _render_lines(
    lines: Sequence[str],
    active_idx: int,
    title_lines: Sequence[str] | None = None,
    color_getter: Callable[[int, bool], tuple[int, int, int]] | None = None,
) -> tuple[int, int]:
    title_lines = title_lines or []
    display.draw.rectangle(
        (0, 0, display.width, display.height),
        outline=0,
        fill=display.current_theme["background"],
    )

    y_offset = 0
    for title_line in title_lines:
        display.draw_shadowed_text(
            display.draw,
            title_line,
            (6, y_offset),
            display.font,
            display.current_theme["highlight"],
            display.current_theme["shadow"],
        )
        y_offset += 10

    usable_height = max(10, display.height - y_offset - 10)
    visible_rows = max(1, usable_height // 10)
    start_idx = max(0, active_idx - visible_rows + 1)
    if start_idx + visible_rows > len(lines):
        start_idx = max(0, len(lines) - visible_rows)
    end_idx = min(len(lines), start_idx + visible_rows)

    for row, index in enumerate(range(start_idx, end_idx)):
        is_active = index == active_idx
        color = (
            color_getter(index, is_active)
            if color_getter is not None
            else display.current_theme["highlight"] if is_active else display.current_theme["text"]
        )
        prefix = "> " if is_active else "  "
        display.draw_shadowed_text(
            display.draw,
            f"{prefix}{lines[index]}",
            (6, y_offset + (row * 10)),
            display.font,
            color,
            display.current_theme["shadow"],
        )

    display.draw_battery_bar()
    display.disp.LCD_ShowImage(display.image, 0, 0)
    return start_idx, end_idx


def select_from_list(
    entries: Sequence[object],
    label_getter: Callable[[object], str],
    title_lines: Sequence[str] | None = None,
    color_getter: Callable[[object, bool], tuple[int, int, int]] | None = None,
) -> int | None:
    if not entries:
        return None

    active_idx = 0

    def wrapped_color(index: int, is_active: bool) -> tuple[int, int, int]:
        if color_getter is None:
            return display.current_theme["highlight"] if is_active else display.current_theme["text"]
        return color_getter(entries[index], is_active)

    while True:
        _wait_until_visible()
        labels = [label_getter(entry) for entry in entries]
        _render_lines(labels, active_idx, title_lines=title_lines, color_getter=wrapped_color)
        active_idx = display.handle_scroll(active_idx, len(entries))

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            setting.debounce()
            return active_idx

        if GPIO.input(setting.KEY1_PIN) == GPIO.LOW or GPIO.input(setting.KEY_LEFT_PIN) == GPIO.LOW:
            setting.debounce()
            return None


def run_action_menu(
    items: Sequence[MenuItem],
    title_lines: Sequence[str] | None = None,
    quick_action: Callable[[], None] | None = None,
) -> ActionResult:
    active_idx = 0

    while True:
        _wait_until_visible()
        labels = [item.text() for item in items]
        _render_lines(labels, active_idx, title_lines=title_lines)
        active_idx = display.handle_scroll(active_idx, len(items))

        if GPIO.input(setting.KEY_PRESS_PIN) == GPIO.LOW:
            setting.debounce()
            action = items[active_idx].action
            if action is None:
                continue
            result = action()
            if result == "back":
                return result

        if quick_action is not None and GPIO.input(setting.KEY1_PIN) == GPIO.LOW:
            setting.debounce()
            quick_action()

        if GPIO.input(setting.KEY_LEFT_PIN) == GPIO.LOW:
            setting.debounce()
            return "back"