"""Generate the application icon (assets/icon.ico)."""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets", "icon.ico")

BG = (11, 15, 20, 255)
SYS = (76, 194, 255, 255)
MIC = (255, 180, 84, 255)
REC = (242, 85, 90, 255)


def render(size: int) -> Image.Image:
    scale = size / 256.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2 * scale, 2 * scale, size - 2 * scale, size - 2 * scale],
                        radius=56 * scale, fill=BG, outline=(36, 48, 61, 255),
                        width=max(1, int(3 * scale)))
    bars = [(64, 52), (96, 96), (128, 132), (160, 74), (192, 104)]
    colors = [SYS, MIC, SYS, MIC, REC]
    width = max(3, int(16 * scale))
    for (x, h), color in zip(bars, colors):
        cx = x * scale
        top = (128 - h / 2) * scale
        bottom = (128 + h / 2) * scale
        d.rounded_rectangle([cx - width / 2, top, cx + width / 2, bottom],
                            radius=width / 2, fill=color)
    return img


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    big = render(256)
    big.save(OUT, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                       (128, 128), (256, 256)])
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
