"""Vertical scrollable list of items with selection and a scrollbar."""
from __future__ import annotations

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import Action


class ScrollList:
    def __init__(self, actions: list[Action]) -> None:
        self.actions = actions
        self.selected = 0
        self._scroll_px = 0.0
        self._scroll_target = 0.0

    # ----- input -----
    def handle(self, ev: ButtonEvent) -> Action | None:
        """Returns an Action if the user activated one (pressed A)."""
        if not ev.pressed or not self.actions:
            return None
        if ev.button is Button.UP:
            self.selected = (self.selected - 1) % len(self.actions)
        elif ev.button is Button.DOWN:
            self.selected = (self.selected + 1) % len(self.actions)
        elif ev.button is Button.LL and not ev.repeat:
            self.selected = max(0, self.selected - 5)
        elif ev.button is Button.RR and not ev.repeat:
            self.selected = min(len(self.actions) - 1, self.selected + 5)
        elif ev.button is Button.A and not ev.repeat:
            return self.actions[self.selected]
        return None

    # ----- render -----
    def render(self, surf: pygame.Surface, rect: pygame.Rect, font: pygame.font.Font) -> None:
        # Update smooth-scroll target so the selected row stays visible.
        row_h = theme.ROW_H
        sel_top = self.selected * row_h
        sel_bot = sel_top + row_h
        if sel_top < self._scroll_target:
            self._scroll_target = float(sel_top)
        elif sel_bot > self._scroll_target + rect.height:
            self._scroll_target = float(sel_bot - rect.height)
        # Ease scroll position toward target.
        self._scroll_px += (self._scroll_target - self._scroll_px) * 0.25

        # Clip to rect so rows above/below the viewport don't bleed.
        prev_clip = surf.get_clip()
        surf.set_clip(rect)

        for i, act in enumerate(self.actions):
            y = rect.y + int(i * row_h - self._scroll_px)
            if y + row_h < rect.y or y > rect.bottom:
                continue
            row = pygame.Rect(rect.x, y, rect.width, row_h)
            selected = i == self.selected
            if selected:
                pygame.draw.rect(surf, theme.SELECTION_BG, row)
                pygame.draw.rect(surf, theme.SELECTION, row, width=2)
            label_color = theme.SELECTION if selected else theme.FG
            label = font.render(act.label, True, label_color)
            surf.blit(label, (row.x + theme.PADDING, row.y + (row_h - label.get_height()) // 2))
            if act.description:
                desc_font = pygame.font.Font(None, theme.FS_SMALL)
                desc = desc_font.render(act.description, True, theme.FG_DIM)
                surf.blit(
                    desc,
                    (
                        row.right - desc.get_width() - theme.PADDING,
                        row.y + (row_h - desc.get_height()) // 2,
                    ),
                )

        surf.set_clip(prev_clip)

        # Scrollbar.
        total_h = len(self.actions) * row_h
        if total_h > rect.height:
            sb_w = 4
            sb_x = rect.right - sb_w - 2
            track = pygame.Rect(sb_x, rect.y, sb_w, rect.height)
            pygame.draw.rect(surf, theme.DIVIDER, track)
            thumb_h = max(20, int(rect.height * rect.height / total_h))
            thumb_y = rect.y + int(self._scroll_px / total_h * rect.height)
            pygame.draw.rect(surf, theme.ACCENT_DIM, pygame.Rect(sb_x, thumb_y, sb_w, thumb_h))
