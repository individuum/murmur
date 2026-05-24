"""Find the monitor that the user is currently working on.

We pick the monitor that contains the *foreground window* — that's the one
the user is interacting with right now, where the dictated text will land.

Pure ctypes (no pywin32 dep). Falls back to primary monitor geometry on
any failure or non-Windows platform.
"""
from __future__ import annotations

import sys


def active_work_area() -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of the work area of the active monitor.

    `work area` excludes the Windows taskbar.
    """
    if sys.platform != "win32":
        return _primary_fallback()

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        hwnd = user32.GetForegroundWindow()
        # If no foreground window, fall back to the monitor under the cursor.
        if not hwnd:
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            MONITOR_DEFAULTTOPRIMARY = 1
            hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTOPRIMARY)
        else:
            MONITOR_DEFAULTTONEAREST = 2
            hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)

        if not hmon:
            return _primary_fallback()

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return _primary_fallback()

        r = mi.rcWork
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        return _primary_fallback()


def _primary_fallback() -> tuple[int, int, int, int]:
    """Single-monitor fallback (uses Tk's screen dimensions)."""
    try:
        import tkinter as tk
        root = tk._default_root  # type: ignore[attr-defined]
        if root is None:
            root = tk.Tk()
            root.withdraw()
            w = root.winfo_screenwidth(); h = root.winfo_screenheight()
            root.destroy()
        else:
            w = root.winfo_screenwidth(); h = root.winfo_screenheight()
        return 0, 0, w, h
    except Exception:
        return 0, 0, 1920, 1080
