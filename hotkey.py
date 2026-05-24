from __future__ import annotations

import threading
from typing import Callable, Optional

from pynput import keyboard


_MODIFIER_ALIASES = {
    "ctrl": keyboard.Key.ctrl,
    "control": keyboard.Key.ctrl,
    "alt": keyboard.Key.alt,
    "alt_gr": keyboard.Key.alt_gr,
    "altgr": keyboard.Key.alt_gr,
    "shift": keyboard.Key.shift,
    "win": keyboard.Key.cmd,
    "cmd": keyboard.Key.cmd,
    "super": keyboard.Key.cmd,
    # Side-specific (use these for PTT-friendly keys most apps ignore).
    "left_ctrl": keyboard.Key.ctrl_l,
    "right_ctrl": keyboard.Key.ctrl_r,
    "left_alt": keyboard.Key.alt_l,
    "right_alt": keyboard.Key.alt_r,
    "left_shift": keyboard.Key.shift_l,
    "right_shift": keyboard.Key.shift_r,
    "left_win": keyboard.Key.cmd_l,
    "right_win": keyboard.Key.cmd_r,
}

_NAMED_KEYS = {
    "space": keyboard.Key.space,
    "tab": keyboard.Key.tab,
    "enter": keyboard.Key.enter,
    "return": keyboard.Key.enter,
    "esc": keyboard.Key.esc,
    "escape": keyboard.Key.esc,
    "backspace": keyboard.Key.backspace,
    "delete": keyboard.Key.delete,
    "home": keyboard.Key.home,
    "end": keyboard.Key.end,
    "page_up": keyboard.Key.page_up,
    "page_down": keyboard.Key.page_down,
    "up": keyboard.Key.up,
    "down": keyboard.Key.down,
    "left": keyboard.Key.left,
    "right": keyboard.Key.right,
    "caps_lock": keyboard.Key.caps_lock,
}
for i in range(1, 25):
    _NAMED_KEYS[f"f{i}"] = getattr(keyboard.Key, f"f{i}", None)
_NAMED_KEYS = {k: v for k, v in _NAMED_KEYS.items() if v is not None}


_LR_NORMALIZE = {
    keyboard.Key.ctrl_l: keyboard.Key.ctrl,
    keyboard.Key.ctrl_r: keyboard.Key.ctrl,
    keyboard.Key.alt_l: keyboard.Key.alt,
    keyboard.Key.alt_r: keyboard.Key.alt,
    keyboard.Key.shift_l: keyboard.Key.shift,
    keyboard.Key.shift_r: keyboard.Key.shift,
    keyboard.Key.cmd_l: keyboard.Key.cmd,
    keyboard.Key.cmd_r: keyboard.Key.cmd,
}


def parse_combo(combo: str) -> frozenset:
    """Parse 'ctrl+alt+space' into a frozenset of normalized pynput keys."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    keys: set = set()
    for part in parts:
        if part in _MODIFIER_ALIASES:
            keys.add(_MODIFIER_ALIASES[part])
        elif part in _NAMED_KEYS:
            keys.add(_NAMED_KEYS[part])
        elif len(part) == 1:
            keys.add(keyboard.KeyCode.from_char(part))
        else:
            raise ValueError(f"Unknown key in combo: {part!r}")
    if not keys:
        raise ValueError("Empty hotkey combo")
    return frozenset(keys)


_SIDED_KEYS = frozenset({
    keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
    keyboard.Key.alt_l, keyboard.Key.alt_r,
    keyboard.Key.shift_l, keyboard.Key.shift_r,
    keyboard.Key.cmd_l, keyboard.Key.cmd_r,
})


def _normalize(key, sided: frozenset = frozenset()) -> Optional[object]:  # noqa: ANN001
    """Normalize a pynput key event.

    If `sided` contains any side-specific variants (e.g. right_alt), don't
    collapse ctrl_l/ctrl_r into the generic ctrl — we need to match exactly.
    """
    if key in _LR_NORMALIZE and key not in sided:
        return _LR_NORMALIZE[key]
    if isinstance(key, keyboard.KeyCode) and key.char is not None:
        return keyboard.KeyCode.from_char(key.char.lower())
    return key


class HotkeyListener:
    """Global push-to-talk hotkey listener.

    Fires `on_activate` when the configured combo is fully pressed, and
    `on_deactivate` when any key in the combo is then released. Auto-repeat
    presses are coalesced so callbacks fire at most once per press cycle.
    """

    def __init__(
        self,
        combo: str,
        on_activate: Callable[[], None],
        on_deactivate: Callable[[], None],
    ) -> None:
        self._combo = parse_combo(combo)
        # If the combo uses side-specific modifiers (e.g. right_alt), tell
        # the normalizer to preserve sides when matching incoming events.
        self._sided = frozenset(k for k in self._combo if k in _SIDED_KEYS)
        self._on_activate = on_activate
        self._on_deactivate = on_deactivate
        self._pressed: set = set()
        self._active = False
        self._listener: Optional[keyboard.Listener] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._pressed.clear()
        self._active = False

    def _on_press(self, key) -> None:  # noqa: ANN001
        k = _normalize(key, self._sided)
        if k is None:
            return
        with self._lock:
            self._pressed.add(k)
            if not self._active and self._combo.issubset(self._pressed):
                self._active = True
                fire = True
            else:
                fire = False
        if fire:
            try:
                self._on_activate()
            except Exception:
                pass

    def _on_release(self, key) -> None:  # noqa: ANN001
        k = _normalize(key, self._sided)
        if k is None:
            return
        with self._lock:
            self._pressed.discard(k)
            if self._active and k in self._combo:
                self._active = False
                fire = True
            else:
                fire = False
        if fire:
            try:
                self._on_deactivate()
            except Exception:
                pass
