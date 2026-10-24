# -*- coding: utf-8 -*-
"""Open the window, walk through idle -> recording -> preview, save screenshots."""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ["WASAPI_RECORDER_NO_ELEVATE"] = "1"
os.environ.setdefault("WASAPI_RECORDER_DATA", os.path.join(ROOT, "_records_test"))

import tkinter as tk  # noqa: E402

import pyaudiowpatch as pyaudio  # noqa: E402
from PIL import ImageGrab  # noqa: E402

import app as app_mod  # noqa: E402

app_mod._enable_dpi_awareness()

from ui import RecorderApp  # noqa: E402

OUT = os.path.join(ROOT, "_shots")


def pump(root, seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        root.update()
        time.sleep(0.02)


def shot(root, name: str) -> None:
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
    path = os.path.join(OUT, name)
    img.save(path)
    print("saved", path, img.size)


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    pa = pyaudio.PyAudio()
    root = tk.Tk()
    app = RecorderApp(root, pa)
    root.attributes("-topmost", True)
    root.update()
    root.lift()
    pump(root, 0.6)
    shot(root, "01-idle.png")

    app._toggle_record()
    pump(root, 1.6)
    shot(root, "02-recording.png")

    app._toggle_record()
    pump(root, 1.2)
    shot(root, "03-preview.png")

    if app.player is not None:
        app._toggle_play()
        pump(root, 0.8)
        shot(root, "04-playing.png")
        app._toggle_play()

    app._on_close()
    print("devices:", {k: (v.short_name if v else None) for k, v in app.devices.items() if k != "errors"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
