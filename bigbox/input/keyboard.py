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
    pygame.K_HOME: Button.HK,
    pygame.K_TAB: Button.SELECT,
}

# Snapshot of the default (PC / GamePi43) layout, used to rebuild the map
# when a different keyboard profile is selected at runtime.
_BASE_KEYMAP = dict(KEYMAP)


def set_keyboard_mode(mode: str = "default") -> None:
    """Swap the key->button map to match a physical keyboard profile.

    "pocketterm": Waveshare PocketTerm35. Its D-pad and A/B/X/Y/L/R game
    buttons are RP2040-mapped to keyboard scancodes — the face buttons
    actually type the letters A, B, X, Y and the shoulders type L/R. So in
    this mode the letter keys A/B/X/Y mean the matching face button, and the
    WASD arrow bindings are dropped (they'd collide with the A button; the
    D-pad already sends real arrow keys).
    """
    global KEYMAP
    if mode != "pocketterm":
        KEYMAP = _BASE_KEYMAP
        return
    m = _BASE_KEYMAP.copy()
    # WASD directionals conflict with the face-button letters on this
    # keyboard (physical A/B/X/Y type letters); the D-pad sends real arrows.
    for k in (pygame.K_w, pygame.K_s, pygame.K_a, pygame.K_d):
        m.pop(k, None)
    m[pygame.K_a] = Button.A
    m[pygame.K_b] = Button.B
    m[pygame.K_x] = Button.X
    m[pygame.K_y] = Button.Y
    KEYMAP = m


def translate(ev: pygame.event.Event, bus: EventBus) -> None:
    if ev.type == pygame.KEYDOWN:
        b = KEYMAP.get(ev.key)
        if b:
            bus.put(ButtonEvent(b, pressed=True, repeat=bool(ev.mod & pygame.KMOD_NONE) and False))
    elif ev.type == pygame.KEYUP:
        b = KEYMAP.get(ev.key)
        if b:
            bus.put(ButtonEvent(b, pressed=False))
