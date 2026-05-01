"""Scan History — browse, view, share, and delete saved ARP/probe scans.

Each entry is a JSON file produced by ``bigbox.scans.save``. Layout
mirrors CapturesView: a file list → action menu → either a result view
(text dump) or webhook share. Webhook send runs on a background thread
so the UI doesn't stall.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from bigbox import scans, theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App


PHASE_LIST = "list"
PHASE_ACTION_MENU = "action_menu"
PHASE_VIEW = "view"


class ScanHistoryView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LIST

        self.list = ScrollList([])
        self.action_list: ScrollList | None = None
        self.target_path: Path | None = None
        self.detail_text: str = ""
        self.detail_title: str = ""
        self.detail_scroll = 0

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.hint_font = pygame.font.Font(None, theme.FS_SMALL)
        self.detail_font = pygame.font.Font(None, 20)

        self._refresh_list()

    def _refresh_list(self) -> None:
        actions: list[Action] = []
        for path in scans.list_saved():
            rec = scans.load(path)
            if rec is None:
                continue
            label = path.name
            desc = f"{rec.type.upper()} :: {rec.summary()}"
            def make_handler(p=path):
                return lambda ctx: self._open_action_menu(p)
            actions.append(Action(label, make_handler(), desc))
        if not actions:
            actions.append(Action("[ No saved scans ]", None))
        self.list = ScrollList(actions)

    def _open_action_menu(self, path: Path) -> None:
        self.target_path = path
        actions = [
            Action("View", lambda ctx: self._view(path)),
            Action("Share via Webhook", lambda ctx: self._share(path)),
            Action("Delete", lambda ctx: self._delete(path)),
        ]
        self.action_list = ScrollList(actions)
        self.phase = PHASE_ACTION_MENU

    def _view(self, path: Path) -> None:
        rec = scans.load(path)
        if rec is None:
            self.detail_text = "Could not load scan."
            self.detail_title = path.name
        else:
            self.detail_text = scans.render_text(rec)
            self.detail_title = path.name
        self.detail_scroll = 0
        self.phase = PHASE_VIEW

    def _delete(self, path: Path) -> None:
        try:
            os.remove(path)
        except Exception as e:
            print(f"[scans] delete failed: {e}")
        self._refresh_list()
        self.phase = PHASE_LIST

    def _share(self, path: Path) -> None:
        from bigbox import webhooks

        def _worker():
            try:
                ok, msg = webhooks.send_file(str(path))
                print(f"[scans] webhook: {msg}")
            except Exception as e:
                print(f"[scans] webhook error: {e}")

        threading.Thread(target=_worker, daemon=True).start()
        self.phase = PHASE_LIST

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return

        if self.phase == PHASE_VIEW:
            if ev.button is Button.B:
                self.phase = PHASE_LIST
            elif ev.button is Button.UP:
                self.detail_scroll = max(0, self.detail_scroll - 60)
            elif ev.button is Button.DOWN:
                self.detail_scroll += 60
            return

        if self.phase == PHASE_ACTION_MENU and self.action_list:
            if ev.button is Button.B:
                self.phase = PHASE_LIST
                return
            action = self.action_list.handle(ev)
            if action and action.handler:
                action.handler(ctx)
            return

        if ev.button is Button.B:
            self.dismissed = True
            return

        action = self.list.handle(ev)
        if action and action.handler:
            action.handler(ctx)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)

        if self.phase == PHASE_VIEW:
            title_text = f"SCAN :: {self.detail_title[:50]}"
        elif self.phase == PHASE_ACTION_MENU:
            name = self.target_path.name if self.target_path else ""
            title_text = f"ACTIONS :: {name}"
        else:
            title_text = "SCAN HISTORY"

        title = self.title_font.render(title_text, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        body_rect = pygame.Rect(theme.PADDING, head_h + theme.PADDING,
                                theme.SCREEN_W - 2 * theme.PADDING,
                                theme.SCREEN_H - head_h - 2 * theme.PADDING - 40)

        if self.phase == PHASE_VIEW:
            self._render_detail(surf, body_rect)
            hint = self.hint_font.render("UP/DOWN: Scroll  B: Back",
                                         True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
            return

        if self.phase == PHASE_ACTION_MENU and self.action_list:
            self.list.render(surf, body_rect, self.body_font)
            overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H),
                                     pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            surf.blit(overlay, (0, 0))
            menu_rect = pygame.Rect(theme.SCREEN_W // 4, theme.SCREEN_H // 4,
                                    theme.SCREEN_W // 2, theme.SCREEN_H // 2)
            pygame.draw.rect(surf, theme.BG_ALT, menu_rect, border_radius=8)
            pygame.draw.rect(surf, theme.ACCENT, menu_rect, 2, border_radius=8)
            header = self.body_font.render("CHOOSE ACTION", True, theme.ACCENT)
            surf.blit(header, (menu_rect.centerx - header.get_width() // 2,
                               menu_rect.y + 10))
            act_rect = pygame.Rect(menu_rect.x + 10, menu_rect.y + 40,
                                   menu_rect.width - 20, menu_rect.height - 60)
            self.action_list.render(surf, act_rect, self.body_font)
            hint = self.hint_font.render("UP/DOWN: Select  A: Execute  B: Cancel",
                                         True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
            return

        self.list.render(surf, body_rect, self.body_font)
        hint = self.hint_font.render("UP/DOWN: Navigate  A: Actions  B: Back",
                                     True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

    def _render_detail(self, surf: pygame.Surface, body: pygame.Rect) -> None:
        pygame.draw.rect(surf, (5, 5, 10), body)
        pygame.draw.rect(surf, theme.DIVIDER, body, 1)
        line_h = 22
        x = body.x + 8
        y = body.y + 6 - self.detail_scroll
        for line in self.detail_text.splitlines():
            if y > body.bottom:
                break
            if y + line_h >= body.y:
                ls = self.detail_font.render(line[:90], True, theme.FG_DIM)
                surf.blit(ls, (x, y))
            y += line_h
