"""Helper for sections to load their visuals (tab-bar icon + page background)
from assets/icons/ in a way that survives moving the install and silently
degrades to None if the file is missing — never crashes import."""
from __future__ import annotations

from pathlib import Path

import pygame

from bigbox import theme

_DIR = Path(__file__).resolve().parents[2] / "assets" / "icons"
_ICON_H = 28   # match the tab bar height

# Page background size = full screen minus the status bar and tab bar.
_BG_W = theme.SCREEN_W
_BG_H = theme.SCREEN_H - theme.STATUS_BAR_H - theme.TAB_BAR_H

# Darkening overlay so foreground text stays readable on busy art.
_DARKEN_ALPHA = 150   # 0 = off, 255 = solid black


def _open(name: str) -> pygame.surface.Surface | None:
    # Try theme icons first
    if theme.ASSETS_ICONS:
        p_theme = Path(theme.ASSETS_ICONS) / f"{name}.png"
        if p_theme.is_file():
            try:
                return pygame.image.load(str(p_theme)).convert_alpha()
            except: pass

    # Fallback to defaults
    p = _DIR / f"{name}.png"
    if not p.is_file():
        return None
    try:
        return pygame.image.load(str(p)).convert_alpha()
    except (pygame.error, FileNotFoundError):
        return None


def load(name: str) -> pygame.surface.Surface | None:
    """Tab-bar icon: scaled to ICON_H tall, aspect preserved."""
    img = _open(name)
    if img is None:
        return None
    if img.get_height() != _ICON_H:
        ratio = _ICON_H / img.get_height()
        img = pygame.transform.smoothscale(
            img, (max(1, int(img.get_width() * ratio)), _ICON_H)
        )
    return img


def load_background(name: str) -> pygame.surface.Surface | None:
    """Page background: cover-fit to BG_W×BG_H with a darkening overlay so
    the title + scroll list stay legible on top."""
    
    # Try theme global background first
    img = None
    if theme.ASSETS_BG:
        p_bg = Path(theme.ASSETS_BG)
        if p_bg.is_file():
            try:
                img = pygame.image.load(str(p_bg)).convert()
            except: pass

    if img is None:
        img = _open(name)
        
    if img is None:
        return None
    iw, ih = img.get_size()
    # cover-fit: scale so the image fills the rect, may crop one axis.
    scale = max(_BG_W / iw, _BG_H / ih)
    new_w, new_h = max(1, int(iw * scale)), max(1, int(ih * scale))
    img = pygame.transform.smoothscale(img, (new_w, new_h))
    # Center-crop into a fresh surface.
    surf = pygame.Surface((_BG_W, _BG_H)).convert()
    surf.blit(img, ((_BG_W - new_w) // 2, (_BG_H - new_h) // 2))
    if _DARKEN_ALPHA > 0:
        overlay = pygame.Surface((_BG_W, _BG_H)).convert()
        overlay.fill((0, 0, 0))
        overlay.set_alpha(_DARKEN_ALPHA)
        surf.blit(overlay, (0, 0))
    return surf
