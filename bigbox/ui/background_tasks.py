"""Tasks view — list everything currently running in the background
and let the user stop them individually.

Reads :mod:`bigbox.background` on every render so the list stays live
as workers come and go. A: stop the highlighted task. X: stop all.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pygame

from bigbox import background, theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


def _format_age(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class BackgroundTasksView:
    def __init__(self) -> None:
        self.dismissed = False
        self.cursor = 0

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.hint_font = pygame.font.Font(None, theme.FS_SMALL)

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        tasks = background.list_tasks()
        if ev.button is Button.B:
            self.dismissed = True
            return
        if not tasks:
            return
        if ev.button is Button.UP:
            self.cursor = (self.cursor - 1) % len(tasks)
        elif ev.button is Button.DOWN:
            self.cursor = (self.cursor + 1) % len(tasks)
        elif ev.button is Button.A:
            target = tasks[self.cursor]
            background.stop_one(target.id)
            self.cursor = max(0, self.cursor - 1)
        elif ev.button is Button.X:
            background.stop_all()
            self.cursor = 0

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title = self.title_font.render("RUNNING TASKS", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        tasks = background.list_tasks()
        if self.cursor >= len(tasks):
            self.cursor = max(0, len(tasks) - 1)

        body_y = head_h + 10
        body_h = theme.SCREEN_H - head_h - 50
        pygame.draw.rect(surf, (5, 5, 10),
                         (theme.PADDING, body_y,
                          theme.SCREEN_W - 2 * theme.PADDING, body_h))
        pygame.draw.rect(surf, theme.DIVIDER,
                         (theme.PADDING, body_y,
                          theme.SCREEN_W - 2 * theme.PADDING, body_h), 1)

        if not tasks:
            msg = self.body_font.render(
                "No background tasks running.", True, theme.FG_DIM)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            body_y + body_h // 2 - msg.get_height() // 2))
            hint = self.hint_font.render("B: Back", True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
            return

        row_h = 38
        now = time.time()
        for i, task in enumerate(tasks):
            y = body_y + 6 + i * row_h
            if y + row_h > body_y + body_h:
                break
            row_rect = pygame.Rect(theme.PADDING + 4, y,
                                   theme.SCREEN_W - 2 * theme.PADDING - 8,
                                   row_h - 4)
            if i == self.cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, row_rect,
                                 border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, row_rect, 2,
                                 border_radius=4)
                label_color = theme.ACCENT
            else:
                label_color = theme.FG

            section_tag = f"[{task.section}] " if task.section else ""
            label = self.body_font.render(
                f"{section_tag}{task.label}"[:60], True, label_color)
            surf.blit(label, (row_rect.x + 8, row_rect.y + 6))

            age = self.hint_font.render(
                _format_age(now - task.started_at), True, theme.FG_DIM)
            surf.blit(age,
                      (row_rect.right - age.get_width() - 8,
                       row_rect.y + 10))

        hint = self.hint_font.render(
            "UP/DOWN: Select   A: Stop   X: Stop All   B: Back",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
