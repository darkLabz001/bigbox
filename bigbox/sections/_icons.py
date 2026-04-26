"""Helper for sections to load their tab icons from assets/icons/ in a way
that survives moving the install (e.g., /opt/bigbox vs the dev tree) and
silently degrades to None if the file is missing — never crashes import."""
from __future__ import annotations

from pathlib import Path

import pygame

_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons"
_ICON_H = 28   # match the tab bar height; will scale to fit


def load(name: str) -> pygame.surface.Surface | None:
    p = _DIR / f"{name}.png"
    if not p.is_file():
        return None
    try:
        img = pygame.image.load(str(p)).convert_alpha()
    except (pygame.error, FileNotFoundError):
        return None
    # Scale to a sensible icon height; preserve aspect.
    if img.get_height() != _ICON_H:
        ratio = _ICON_H / img.get_height()
        img = pygame.transform.smoothscale(
            img, (max(1, int(img.get_width() * ratio)), _ICON_H)
        )
    return img
