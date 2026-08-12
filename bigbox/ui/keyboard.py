"""On-screen keyboard for text input on handheld devices."""
from __future__ import annotations

import pygame
from typing import Callable

from bigbox import theme
from bigbox.events import Button, ButtonEvent


class KeyboardView:
    """Handheld-optimized on-screen keyboard."""

    LAYOUT_LOWER = [
        ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
        ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
        ["a", "s", "d", "f", "g", "h", "j", "k", "l", "/"],
        ["SHIFT", "z", "x", "c", "v", "b", "n", "m", ".", "BSPC"],
        ["SYMBOL", "SPACE", "CANCEL", "DONE"]
    ]

    LAYOUT_UPPER = [
        ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"],
        ["Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P"],
        ["A", "S", "D", "F", "G", "H", "J", "K", "L", "?"],
        ["shift", "Z", "X", "C", "V", "B", "N", "M", ",", "BSPC"],
        ["SYMBOL", "SPACE", "CANCEL", "DONE"]
    ]

    LAYOUT_SYMBOL = [
        ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
        ["-", "/", ":", ";", "(", ")", "$", "&", "@", "\""],
        [".", ",", "?", "!", "'", "[", "]", "{", "}", "\\"],
        ["ABC", "_", "=", "+", "*", "<", ">", "|", "~", "BSPC"],
        ["ABC", "SPACE", "CANCEL", "DONE"]
    ]

    def __init__(self, title: str, callback: Callable[[str | None], None], initial_text: str = "") -> None:
        self.title = title
        self.callback = callback
        self.text = initial_text
        self.cursor_x = 0
        self.cursor_y = 0
        self.mode = "lower" # lower, upper, symbol
        self.dismissed = False
        
        self.layout = self.LAYOUT_LOWER

    def _get_layout(self):
        if self.mode == "lower": return self.LAYOUT_LOWER
        if self.mode == "upper": return self.LAYOUT_UPPER
        return self.LAYOUT_SYMBOL

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed: return
        
        layout = self._get_layout()
        rows = len(layout)
        cols = len(layout[self.cursor_y])

        if ev.button is Button.UP:
            self.cursor_y = (self.cursor_y - 1) % rows
            # Adjust x if the new row is shorter
            self.cursor_x = min(self.cursor_x, len(layout[self.cursor_y]) - 1)
        elif ev.button is Button.DOWN:
            self.cursor_y = (self.cursor_y + 1) % rows
            self.cursor_x = min(self.cursor_x, len(layout[self.cursor_y]) - 1)
        elif ev.button is Button.LEFT:
            self.cursor_x = (self.cursor_x - 1) % len(layout[self.cursor_y])
        elif ev.button is Button.RIGHT:
            self.cursor_x = (self.cursor_x + 1) % len(layout[self.cursor_y])
        elif ev.button is Button.A:
            key = layout[self.cursor_y][self.cursor_x]
            self._press_key(key)
        elif ev.button in (Button.B, Button.SELECT):
            # Cancel. On the PocketTerm35 the gamepad B == the letter-B key, so
            # in a text box B types 'b'; the SELECT button sends a distinct code
            # and is the reliable "exit" button (Esc on the QWERTY also cancels).
            self.callback(None)
            self.dismissed = True
        elif ev.button is Button.START:
            self.callback(self.text) # Done / submit
            self.dismissed = True
        elif ev.button is Button.X: # Quick Backspace
            if len(self.text) > 0:
                self.text = self.text[:-1]

    _SHIFT_MAP = {
        "1": "!", "2": "@", "3": "#", "4": "$", "5": "%", "6": "^", "7": "&",
        "8": "*", "9": "(", "0": ")", "-": "_", "=": "+", "[": "{", "]": "}",
        "\\": "|", ";": ":", "'": "\"", ",": "<", ".": ">", "/": "?", "`": "~",
    }

    def key_event(self, ev) -> None:
        """Handle a physical KEYDOWN while the keyboard is open.

        Uses pygame.key.name()+Shift rather than ev.unicode, which is empty on
        the device's console/KMSDRM SDL backend. The caller routes every
        non-arrow key here and consumes it, so no key can fire a game button or
        the HK system menu while typing.
        """
        k = ev.key
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.callback(self.text)
            self.dismissed = True
            return
        if k in (pygame.K_ESCAPE, pygame.K_TAB):   # Esc / Select -> cancel
            self.callback(None)
            self.dismissed = True
            return
        if k == pygame.K_BACKSPACE:
            self.text = self.text[:-1]
            return
        if k in (pygame.K_UP, pygame.K_DOWN, pygame.K_LEFT, pygame.K_RIGHT):
            return  # navigation is handled via translated button events
        name = pygame.key.name(k)
        shift = bool(pygame.key.get_mods() & pygame.KMOD_SHIFT)
        if name == "space":
            self.text += " "
        elif len(name) == 1:
            if name.isalpha():
                self.text += name.upper() if shift else name
            elif shift and name in self._SHIFT_MAP:
                self.text += self._SHIFT_MAP[name]
            else:
                self.text += name

    def handle_touch(self, x: int, y: int) -> None:
        # No on-screen grid anymore — text comes from the physical keyboard.
        return

    def _press_key(self, key: str):
        if key == "SHIFT":
            self.mode = "upper"
        elif key == "shift":
            self.mode = "lower"
        elif key == "SYMBOL":
            self.mode = "symbol"
        elif key == "ABC":
            self.mode = "lower"
        elif key == "BSPC":
            if len(self.text) > 0:
                self.text = self.text[:-1]
        elif key == "SPACE":
            self.text += " "
        elif key == "DONE":
            self.callback(self.text)
            self.dismissed = True
        elif key == "CANCEL":
            self.callback(None)
            self.dismissed = True
        else:
            self.text += key
            # Auto-revert shift after one key? (Like most OSKs)
            if self.mode == "upper":
                self.mode = "lower"

    def render(self, surf: pygame.Surface) -> None:
        # Dim background
        overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        surf.blit(overlay, (0, 0))

        # Compact input dialog — you type on the device's physical keyboard.
        box_w = min(640, theme.SCREEN_W - 40)
        box_h = 172
        box = pygame.Rect((theme.SCREEN_W - box_w) // 2,
                          (theme.SCREEN_H - box_h) // 2, box_w, box_h)
        pygame.draw.rect(surf, theme.BG, box, border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT, box, 2, border_radius=10)

        # Title
        f_title = pygame.font.Font(None, 30)
        surf.blit(f_title.render(self.title, True, theme.ACCENT), (box.x + 20, box.y + 18))

        # Text field with blinking caret; scrolls to keep the caret visible.
        input_rect = pygame.Rect(box.x + 20, box.y + 60, box.width - 40, 54)
        pygame.draw.rect(surf, theme.BG_ALT, input_rect, border_radius=6)
        pygame.draw.rect(surf, theme.DIVIDER, input_rect, 1, border_radius=6)
        f_text = pygame.font.Font(None, 38)
        shown = self.text + ("_" if int(time.time() * 2) % 2 == 0 else " ")
        while f_text.size(shown)[0] > input_rect.width - 24 and len(shown) > 1:
            shown = shown[1:]
        surf.blit(f_text.render(shown, True, theme.FG), (input_rect.x + 12, input_rect.y + 12))

        # Hint
        f_hint = pygame.font.Font(None, 20)
        hint = f_hint.render("Type on the keyboard    Enter: OK    Esc: Cancel",
                             True, theme.FG_DIM)
        surf.blit(hint, (box.x + 20, box.bottom - 28))
import time
