"""Path helpers that work the same in dev (python app.py) and when frozen
into a PyInstaller .exe.

- `app_dir()`   — writable directory next to the script / next to the .exe.
                  This is where config.json, last-recording.wav, and the log live.
- `bundled(name)` — read-only resource shipped inside the exe (or alongside
                  the script in dev).
"""
from __future__ import annotations

import sys
from pathlib import Path


def app_dir() -> Path:
    """Where mutable user files live (config.json, logs, recordings)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled(name: str) -> Path:
    """Path to a read-only resource that ships with the app."""
    if getattr(sys, "frozen", False):
        # PyInstaller extracts bundled data to sys._MEIPASS at runtime.
        return Path(sys._MEIPASS) / name  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent / name
