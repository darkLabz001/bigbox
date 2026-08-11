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
        """Press the on-screen key at canvas coords (x, y). Mirrors render()."""
        kb_w = min(700, theme.SCREEN_W - 40)
        kb_h = 360
        kb_x = (theme.SCREEN_W - kb_w) // 2
        kb_y = (theme.SCREEN_H - kb_h) // 2
        key_start_y = (kb_y + 55 + 50) + 20   # input box bottom + 20
        key_margin = 8
        key_h = 40
        layout = self._get_layout()
        for r, row in enumerate(layout):
            row_w = kb_w - 40
            key_w = (row_w - (len(row) - 1) * key_margin) // len(row)
            ky = key_start_y + r * (key_h + key_margin)
            if not (ky <= y < ky + key_h):
                continue
            for c, key in enumerate(row):
                kx = kb_x + 20 + c * (key_w + key_margin)
                if kx <= x < kx + key_w:
                    self.cursor_y, self.cursor_x = r, c
                    self._press_key(key)
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
        # Darken background
        overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        surf.blit(overlay, (0, 0))

        # Main Box
        kb_w, kb_h = min(700, theme.SCREEN_W - 40), 360
        kb_rect = pygame.Rect((theme.SCREEN_W - kb_w)//2, (theme.SCREEN_H - kb_h)//2, kb_w, kb_h)
        pygame.draw.rect(surf, theme.BG, kb_rect, border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT, kb_rect, 2, border_radius=10)

        # Title
        f_title = pygame.font.Font(None, 32)
        title_surf = f_title.render(self.title, True, theme.ACCENT)
        surf.blit(title_surf, (kb_rect.x + 20, kb_rect.y + 15))

        # Text Input Box
        input_rect = pygame.Rect(kb_rect.x + 20, kb_rect.y + 55, kb_rect.width - 40, 50)
        pygame.draw.rect(surf, theme.BG_ALT, input_rect, border_radius=5)
        pygame.draw.rect(surf, theme.DIVIDER, input_rect, 1, border_radius=5)
        
        f_text = pygame.font.Font(None, 36)
        display_text = self.text + ("_" if int(time.time()*2)%2 == 0 else " ")
        text_surf = f_text.render(display_text, True, theme.FG)
        surf.blit(text_surf, (input_rect.x + 10, input_rect.y + 10))

        # Keys
        layout = self._get_layout()
        key_margin = 8
        key_start_y = input_rect.bottom + 20
        
        for r, row in enumerate(layout):
            row_w = kb_rect.width - 40
            key_w = (row_w - (len(row)-1)*key_margin) // len(row)
            key_h = 40
            
            for c, key in enumerate(row):
                is_selected = (r == self.cursor_y and c == self.cursor_x)
                
                kx = kb_rect.x + 20 + c * (key_w + key_margin)
                ky = key_start_y + r * (key_h + key_margin)
                
                # Dynamic width for bottom row
                if r == 4:
                    if key in ["SPACE", "DONE"]:
                        # Space and Done are double wide in our logic? No, let's keep it simple for now.
                        pass

                k_rect = pygame.Rect(kx, ky, key_w, key_h)
                
                bg_color = theme.SELECTION_BG if is_selected else theme.BG_ALT
                border_color = theme.ACCENT if is_selected else theme.DIVIDER
                
                pygame.draw.rect(surf, bg_color, k_rect, border_radius=5)
                pygame.draw.rect(surf, border_color, k_rect, 2 if is_selected else 1, border_radius=5)
                
                f_key = pygame.font.Font(None, 24)
                key_label = f_key.render(key, True, theme.ACCENT if is_selected else theme.FG)
                surf.blit(key_label, (k_rect.centerx - key_label.get_width()//2, k_rect.centery - key_label.get_height()//2))

        # Footer Hint
        f_hint = pygame.font.Font(None, 18)
        hint = f_hint.render("D-PAD: Navigate  A: Select  X: Backspace  B: Cancel", True, theme.FG_DIM)
        surf.blit(hint, (kb_rect.x + 20, kb_rect.bottom - 25))
import time
