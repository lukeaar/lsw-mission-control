"""The palette. Every renderer reads colours from `C` at call time, so a config's [theme] applies
everywhere (defaults included: no colour is bound into a default argument)."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Theme:
    bg: str
    surface: str
    border: str
    text: str
    muted: str  # text at 60% over the background
    faint: str  # text at 40% over the background
    accent: str
    accent_soft: str
    green: str
    amber: str
    red: str
    red_soft: str


# The LSW dark palette: a warm near-black ground, a violet accent.
LSW_DARK = Theme(bg="#252226", surface="#3e3940", border="#575059", text="#fdfbf9", muted="#a7a4a5",
                 faint="#7b797a", accent="#c761f2", accent_soft="#e9c2f9", green="#3fb950", amber="#d29922",
                 red="#ef4444", red_soft="#ff6b6b")

THEME_KEYS = tuple(f.name for f in fields(Theme))
_HEX = re.compile(r"#[0-9a-fA-F]{6}")


class _Palette:
    """The theme in use, as upper-case attributes: C.GREEN, C.FAINT, ..."""

    BG: str
    SURFACE: str
    BORDER: str
    TEXT: str
    MUTED: str
    FAINT: str
    ACCENT: str
    ACCENT_SOFT: str
    GREEN: str
    AMBER: str
    RED: str
    RED_SOFT: str

    def __init__(self, theme: Theme) -> None:
        self.use(theme)

    def use(self, theme: Theme) -> None:
        for name in THEME_KEYS:
            setattr(self, name.upper(), getattr(theme, name))


C = _Palette(LSW_DARK)


def _luminance(colour: str) -> float:
    """WCAG relative luminance of '#rrggbb'."""
    c = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio of two '#rrggbb' colours (1 to 21)."""
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def ink_on(colour: str) -> str:
    """The theme's background or text colour, whichever reads better on `colour`: a chip's letters
    stay legible in a dark theme and in a light one."""
    return C.BG if contrast(C.BG, colour) >= contrast(C.TEXT, colour) else C.TEXT


def use(theme: Theme) -> None:
    """Switch the palette (once, at start-up: a config change restarts the process)."""
    C.use(theme)


def theme_from(overrides: dict) -> Theme:
    """LSW_DARK with a config's [theme] overrides (each '#rrggbb')."""
    values = {name: getattr(LSW_DARK, name) for name in THEME_KEYS}
    for key, value in overrides.items():
        if key not in values:
            raise ValueError(f"unknown key [theme].{key}")
        if not isinstance(value, str) or not _HEX.fullmatch(value):
            raise ValueError(f"[theme].{key} must be a colour like '#252226'")
        values[key] = value
    return Theme(**values)
