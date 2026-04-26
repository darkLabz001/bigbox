"""Pygame keyboard fallback for development on a regular PC."""
from __future__ import annotations

import pygame

from bigbox.events import Button, ButtonEvent, EventBus

KEYMAP: dict[int, Button] = {
    pygame.K_UP: Button.UP,
    pygame.K_DOWN: Button.DOWN,
    pygame.K_LEFT: Button.LEFT,
    pygame.K_RIGHT: Button.RIGHT,
    pygame.K_z: Button.A,
    pygame.K_x: Button.B,
    pygame.K_a: Button.X,
    pygame.K_s: Button.Y,
    pygame.K_q: Button.LL,
    pygame.K_w: Button.RR,
    pygame.K_h: Button.HK,
    pygame.K_RETURN: Button.START,
    pygame.K_RSHIFT: Button.SELECT,
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
