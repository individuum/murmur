"""Tray icons — modern, flat, monochrome on a soft tinted square."""
from __future__ import annotations

from PIL import Image, ImageDraw

SIZE = 64

# Apple-ish palette
_IDLE         = (38, 38, 40, 255)     # dark surface
_RECORDING    = (255, 69, 58, 255)    # systemRed
_TRANSCRIBING = (255, 159, 10, 255)   # systemOrange
_LISTENING    = (10, 132, 255, 255)   # systemBlue


def _draw_mic(bg: tuple[int, int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Soft rounded square background
    d.rounded_rectangle((4, 4, SIZE - 4, SIZE - 4), radius=16, fill=bg)
    # Microphone glyph — clean, single-weight, white
    cx = SIZE // 2
    cap_top, cap_bottom = 18, 38
    cap_w = 14
    d.rounded_rectangle(
        (cx - cap_w // 2, cap_top, cx + cap_w // 2, cap_bottom),
        radius=cap_w // 2, fill="white",
    )
    # Stand
    d.arc((cx - 13, cap_bottom - 9, cx + 13, cap_bottom + 11),
          0, 180, fill="white", width=3)
    d.line((cx, cap_bottom + 7, cx, cap_bottom + 14), fill="white", width=3)
    d.line((cx - 6, cap_bottom + 14, cx + 6, cap_bottom + 14), fill="white", width=3)
    return img


def idle() -> Image.Image:
    return _draw_mic(_IDLE)


def recording() -> Image.Image:
    return _draw_mic(_RECORDING)


def transcribing() -> Image.Image:
    return _draw_mic(_TRANSCRIBING)


def listening() -> Image.Image:
    return _draw_mic(_LISTENING)
