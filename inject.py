from __future__ import annotations

import time

import pyperclip
from pynput.keyboard import Controller, Key


_kb = Controller()


def inject_paste(text: str) -> None:
    """Save clipboard → set to `text` → send Ctrl+V → restore clipboard.

    Works in any focused window that accepts text, including terminals
    (Windows Terminal, VS Code terminal, etc.) where UIA-based injection fails.
    """
    if not text:
        return
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None

    pyperclip.copy(text)
    # Small delay so the clipboard is settled before paste fires.
    time.sleep(0.04)
    with _kb.pressed(Key.ctrl):
        _kb.press("v")
        _kb.release("v")
    # Give the target app time to consume the paste before we restore.
    time.sleep(0.15)

    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass


def inject_keystrokes(text: str) -> None:
    """Type each character via SendInput. Slower, but no clipboard side-effect."""
    if not text:
        return
    _kb.type(text)


def inject(text: str, method: str = "paste") -> None:
    if method == "keystrokes":
        inject_keystrokes(text)
    else:
        inject_paste(text)
