"""Small tkinter widgets: flat buttons, level meters, sliders, progress bar.

Everything is drawn on Canvas because ttk on Windows cannot be themed
consistently across light/dark desktops.
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
import typing as t

# --------------------------------------------------------------------------- palette
BG = "#0B0F14"
PANEL = "#131A22"
PANEL_2 = "#1C2632"
TRACK = "#0C1319"
LINE = "#24303D"
TEXT = "#E7EEF5"
MUTED = "#8B9AAB"
FAINT = "#576577"
SYS = "#4CC2FF"
MIC = "#FFB454"
REC = "#F2555A"
OK = "#3DD68C"
WARN = "#F0B429"

_FAMILY: str | None = None


def font_family() -> str:
    global _FAMILY
    if _FAMILY is None:
        families = set(tkfont.families())
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
            if name in families:
                _FAMILY = name
                break
        else:
            _FAMILY = "TkDefaultFont"
    return _FAMILY


def font(size: int, bold: bool = False) -> tuple:
    return (font_family(), size, "bold" if bold else "normal")


def mono(size: int, bold: bool = True) -> tuple:
    return ("Consolas", size, "bold" if bold else "normal")


def _shade(color: str, factor: float) -> str:
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    if factor >= 0:
        r = int(r + (255 - r) * factor)
        g = int(g + (255 - g) * factor)
        b = int(b + (255 - b) * factor)
    else:
        r, g, b = (int(v * (1 + factor)) for v in (r, g, b))
    return "#%02X%02X%02X" % (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def rounded_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                 radius: float, **kwargs) -> int:
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=20, **kwargs)


# --------------------------------------------------------------------------- button
class FlatButton(tk.Canvas):
    def __init__(self, master, text: str, command: t.Callable[[], None] | None = None,
                 width: int = 150, height: int = 40, *, bg: str = PANEL_2,
                 fg: str = TEXT, hover: str | None = None, radius: int = 10,
                 text_font: tuple | None = None, border: str | None = None):
        super().__init__(master, width=width, height=height, bg=master.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2")
        self._text = text
        self._command = command
        self._bg = bg
        self._hover = hover or _shade(bg, 0.07)
        self._fg = fg
        self._radius = radius
        self._font = text_font or font(10, True)
        self._border = border
        self._enabled = True
        self._selected = False
        self._hovering = False
        self._pressed = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda _e: self._redraw())
        self._redraw()

    # -- api
    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self._enabled:
            self._enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            self._redraw()

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self._redraw()

    def set_colors(self, bg: str | None = None, fg: str | None = None) -> None:
        if bg:
            self._bg = bg
            self._hover = _shade(bg, 0.08)
        if fg:
            self._fg = fg
        self._redraw()

    # -- events
    def _on_enter(self, _event) -> None:
        self._hovering = True
        self._redraw()

    def _on_leave(self, _event) -> None:
        self._hovering = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _event) -> None:
        if not self._enabled:
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, _event) -> None:
        was = self._pressed
        self._pressed = False
        self._redraw()
        if was and self._enabled and self._command is not None:
            self._command()

    # -- drawing
    def _redraw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), int(self["width"]))
        h = max(self.winfo_height(), int(self["height"]))
        if self._selected:
            fill = _shade(self._bg, 0.12)
        elif not self._enabled:
            fill = _shade(self._bg, -0.45)
        elif self._pressed:
            fill = _shade(self._bg, -0.12)
        elif self._hovering:
            fill = self._hover
        else:
            fill = self._bg
        rounded_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill,
                     outline=self._border or "", width=1)
        color = self._fg if self._enabled else FAINT
        self.create_text(w / 2, h / 2 + 1, text=self._text, fill=color, font=self._font)


# --------------------------------------------------------------------------- meter
class Meter(tk.Canvas):
    """Peak meter with a decaying bar and a peak-hold marker."""

    def __init__(self, master, color: str = SYS, height: int = 16):
        super().__init__(master, height=height, bg=master.cget("bg"),
                         highlightthickness=0, bd=0)
        self.color = color
        self._target = 0.0
        self._level = 0.0
        self._hold = 0.0
        self._hold_age = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_level(self, value: float) -> None:
        self._target = max(0.0, min(1.0, float(value)))
        if self._target > self._level:
            self._level = self._target
        if self._target > self._hold:
            self._hold = self._target
            self._hold_age = 0.0

    def decay(self, dt: float = 0.04) -> None:
        if self._target < self._level:
            self._level = max(self._target, self._level - dt * 1.8)
        self._hold_age += dt
        if self._hold_age > 0.9:
            self._hold = max(self._level, self._hold - dt * 0.6)
        self._redraw()

    @staticmethod
    def _norm(value: float) -> float:
        if value <= 0.0005:
            return 0.0
        db = 20 * math.log10(value)
        return max(0.0, min(1.0, (db + 60.0) / 60.0))

    def _redraw(self) -> None:
        self.delete("all")
        w = max(10, self.winfo_width())
        h = max(6, self.winfo_height())
        rounded_rect(self, 0, 0, w, h, h / 2, fill=TRACK, outline="")
        value = self._norm(self._level)
        if value > 0.005:
            x = 2 + (w - 4) * value
            zones = ((0.0, 0.55, OK), (0.55, 0.80, WARN), (0.80, 1.0, REC))
            for start, end, color in zones:
                x1 = 2 + (w - 4) * start
                x2 = min(x, 2 + (w - 4) * end)
                if x2 > x1:
                    rounded_rect(self, x1, 1, x2, h - 1, (h - 2) / 2, fill=color, outline="")
        hold = self._norm(self._hold)
        if hold > 0.01:
            hx = 2 + (w - 4) * hold
            color = REC if hold > 0.8 else (WARN if hold > 0.55 else _shade(self.color, 0.25))
            self.create_line(hx, 1, hx, h - 1, fill=color, width=2)


# --------------------------------------------------------------------------- slider
class HSlider(tk.Canvas):
    def __init__(self, master, value: int = 100, minimum: int = 0, maximum: int = 200,
                 command: t.Callable[[int], None] | None = None, color: str = SYS,
                 width: int = 380, height: int = 28):
        super().__init__(master, width=width, height=height, bg=master.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.minimum = minimum
        self.maximum = maximum
        self._value = value
        self._command = command
        self._color = color
        self._enabled = True
        self._drag = False
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<MouseWheel>", self._on_wheel)

    # -- api
    def get(self) -> int:
        return self._value

    def set(self, value: int) -> None:
        value = max(self.minimum, min(self.maximum, int(value)))
        if value != self._value:
            self._value = value
            self._redraw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._redraw()

    def set_color(self, color: str) -> None:
        self._color = color
        self._redraw()

    # -- events
    def _value_from_x(self, x: float) -> int:
        pad = 12
        w = max(20, self.winfo_width())
        ratio = (x - pad) / max(1, w - 2 * pad)
        ratio = max(0.0, min(1.0, ratio))
        return int(round(self.minimum + ratio * (self.maximum - self.minimum)))

    def _apply(self, x: float) -> None:
        value = self._value_from_x(x)
        if value != self._value:
            self._value = value
            self._redraw()
            if self._command is not None:
                self._command(value)

    def _on_press(self, event) -> None:
        if not self._enabled:
            return
        self._drag = True
        self._apply(event.x)

    def _on_drag(self, event) -> None:
        if self._drag and self._enabled:
            self._apply(event.x)

    def _on_release(self, _event) -> None:
        self._drag = False

    def _on_wheel(self, event) -> None:
        if not self._enabled:
            return
        step = 5 if event.delta > 0 else -5
        new = max(self.minimum, min(self.maximum, self._value + step))
        if new != self._value:
            self._value = new
            self._redraw()
            if self._command is not None:
                self._command(new)

    # -- drawing
    def _redraw(self) -> None:
        self.delete("all")
        w = max(20, self.winfo_width())
        h = max(10, self.winfo_height())
        cy = h / 2
        pad = 12
        rounded_rect(self, pad, cy - 3, w - pad, cy + 3, 3,
                     fill=_shade(TRACK, 0.10) if self._enabled else TRACK, outline="")
        ratio = (self._value - self.minimum) / max(1, (self.maximum - self.minimum))
        knob_x = pad + (w - 2 * pad) * ratio
        color = self._color if self._enabled else FAINT
        if knob_x > pad + 1:
            rounded_rect(self, pad, cy - 3, knob_x, cy + 3, 3, fill=color, outline="")
        self.create_oval(knob_x - 8, cy - 8, knob_x + 8, cy + 8,
                         fill=BG, outline=color, width=3)


# --------------------------------------------------------------------------- progress
class ProgressBar(tk.Canvas):
    def __init__(self, master, height: int = 6, color: str = SYS):
        super().__init__(master, height=height, bg=master.cget("bg"),
                         highlightthickness=0, bd=0)
        self._value = 0.0
        self.color = color
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if abs(value - self._value) > 0.001:
            self._value = value
            self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = max(10, self.winfo_width())
        h = max(4, self.winfo_height())
        rounded_rect(self, 0, 0, w, h, h / 2, fill=TRACK, outline="")
        if self._value > 0.002:
            rounded_rect(self, 0, 0, max(h, w * self._value), h, h / 2,
                         fill=self.color, outline="")


class Divider(tk.Frame):
    def __init__(self, master, **kwargs):
        super().__init__(master, bg=LINE, height=1, **kwargs)
