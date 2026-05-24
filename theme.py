"""Centralized design tokens — Apple-inspired dark theme.

Lazy-imports `customtkinter` inside the font helpers so this module can be
imported before Tk is initialized (e.g. by tooling).
"""
from __future__ import annotations


# ── Surfaces (Apple "dark" semantic colors, adjusted slightly warmer) ─────────
BG_BASE      = "#1a1a1c"   # Window background
BG_CARD      = "#262628"   # Card / grouped section background
BG_INPUT     = "#2f2f31"   # Text fields, dropdowns
BG_HOVER     = "#3a3a3c"   # Hover state
SEPARATOR    = "#38383a"   # 1px hairline divider

# ── Text ─────────────────────────────────────────────────────────────────────
TEXT_PRIMARY    = "#ffffff"
TEXT_SECONDARY  = "#a1a1a6"
TEXT_TERTIARY   = "#6e6e73"
TEXT_INVERSE    = "#1a1a1c"

# ── Accent (Apple "systemBlue" — dark variant) ───────────────────────────────
ACCENT          = "#0A84FF"
ACCENT_HOVER    = "#0070dc"

# ── Semantic ─────────────────────────────────────────────────────────────────
SUCCESS = "#30D158"
WARNING = "#FF9F0A"
DANGER  = "#FF453A"
LISTEN  = "#64D2FF"

# ── Spacing scale ────────────────────────────────────────────────────────────
S_XS, S_SM, S_MD, S_LG, S_XL, S_XXL = 4, 8, 12, 20, 28, 40

# ── Radius scale ─────────────────────────────────────────────────────────────
R_SM, R_MD, R_LG, R_XL = 10, 12, 20, 28

# ── Typography helpers ───────────────────────────────────────────────────────
# Win11 ships "Segoe UI Variable" with three optical sizes:
#   Display (large headlines), Text (body), Small (captions).
# Falls back gracefully to plain "Segoe UI" if Variable isn't installed.
_DISPLAY = "Segoe UI Variable Display"
_TEXT    = "Segoe UI Variable Text"
_SMALL   = "Segoe UI Variable Small"


def display(size: int = 24, weight: str = "bold"):
    import customtkinter as ctk
    return ctk.CTkFont(family=_DISPLAY, size=size, weight=weight)


def title(size: int = 16, weight: str = "bold"):
    import customtkinter as ctk
    return ctk.CTkFont(family=_TEXT, size=size, weight=weight)


def body(size: int = 13, weight: str = "normal"):
    import customtkinter as ctk
    return ctk.CTkFont(family=_TEXT, size=size, weight=weight)


def caption(size: int = 11):
    import customtkinter as ctk
    return ctk.CTkFont(family=_SMALL, size=size)


def mono(size: int = 12):
    import customtkinter as ctk
    return ctk.CTkFont(family="Cascadia Code", size=size)
