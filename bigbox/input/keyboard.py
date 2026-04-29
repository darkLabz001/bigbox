"""Pygame keyboard fallback for development on a regular PC."""
from __future__ import annotations

import pygame

from bigbox.events import Button, ButtonEvent, EventBus

KEYMAP: dict[int, Button] = {
    pygame.K_UP: Button.UP,
    pygame.K_DOWN: Button.DOWN,
    pygame.K_LEFT: Button.LEFT,
    pygame.K_RIGHT: Button.RIGHT,
    
    # WASD support
    pygame.K_w: Button.UP,
    pygame.K_s: Button.DOWN,
    pygame.K_a: Button.LEFT,
    pygame.K_d: Button.RIGHT,
    
    # Primary mappings (matches README.md table)
    pygame.K_z: Button.A,
    pygame.K_x: Button.B,
    pygame.K_c: Button.X,
    pygame.K_v: Button.Y,
    
    # Intuitive face button fallbacks
    pygame.K_SPACE: Button.A,
    pygame.K_ESCAPE: Button.B,
    pygame.K_BACKSPACE: Button.SELECT,
    pygame.K_RETURN: Button.START,
    
    # Shoulder buttons
    pygame.K_q: Button.LL,
    pygame.K_e: Button.RR,
    pygame.K_l: Button.LL,
    pygame.K_r: Button.RR,
    
    # System
    pygame.K_h: Button.HK,
    pygame.K_TAB: Button.SELECT,
}


def translate(ev: pygame.event.Event, bus: EventBus) -> None:
    if ev.type == pygame.KEYDOWN:
        b = KEYMAP.get(ev.key)
        if b:
            bus.put(ButtonEvent(b, pressed=True, repeat=bool(ev.mod & pygame.KMOD_NONE) and False))
    elif ev.type == pygame.KEYUP:
        b = KEYMAP.get(ev.key)
        if b:
            bus.put(ButtonEvent(b, pressed=False))
