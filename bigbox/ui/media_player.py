"""Media Player — File browser and playback UI."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App


class MediaPlayerView:
    def __init__(self, media_dir: str = "media") -> None:
        self.media_dir = media_dir
        try:
            if not os.path.exists(self.media_dir):
                os.makedirs(self.media_dir)
        except Exception as e:
            print(f"[media] Failed to create dir: {e}")

        self.dismissed = False
        self.playing_file: str | None = None
        self.list = self._refresh_list()
        
        # Cache fonts to avoid re-loading every frame
        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.hint_font = pygame.font.Font(None, theme.FS_SMALL)
        self.play_font = pygame.font.Font(None, 100)

    def _refresh_list(self) -> ScrollList:
        files = []
        try:
            if os.path.exists(self.media_dir):
                files = sorted([f for f in os.listdir(self.media_dir) if os.path.isfile(os.path.join(self.media_dir, f))])
        except Exception as e:
            print(f"[media] List error: {e}")
        
        actions = []
        for f in files:
            # Closure to capture f
            def make_handler(filename: str):
                return lambda ctx: self._play(filename)
            
            actions.append(Action(f, make_handler(f)))
        
        if not actions:
            actions.append(Action("[ No media found ]", None, "Upload via Web UI"))
            
        return ScrollList(actions)

    def _play(self, filename: str) -> None:
        self.playing_file = filename

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        try:
            if not ev.pressed:
                return

            if ev.button is Button.B:
                if self.playing_file:
                    self.playing_file = None
                else:
                    self.dismissed = True
                return

            if self.playing_file:
                # Controls while playing
                if ev.button is Button.A:
                    self.playing_file = None # Stop
                return

            # File list handling
            action = self.list.handle(ev)
            if action and action.handler:
                action.handler(ctx)
        except Exception as e:
            print(f"[media] Handle error: {e}")

    def render(self, surf: pygame.Surface) -> None:
        try:
            surf.fill(theme.BG)
            
            # Header
            head_h = 60
            pygame.draw.rect(surf, theme.BG_DIM, (0, 0, theme.SCREEN_W, head_h))
            pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1), (theme.SCREEN_W, head_h - 1), 2)
            
            title_text = "MEDIA PLAYER" if not self.playing_file else f"PLAYING: {self.playing_file}"
            title = self.title_font.render(title_text, True, theme.ACCENT)
            surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

            if self.playing_file:
                # Playback UI (Placeholder)
                center_x, center_y = theme.SCREEN_W // 2, theme.SCREEN_H // 2
                
                # Draw a "TV" or "Screen" box
                box_w, box_h = 600, 340
                box = pygame.Rect(center_x - box_w // 2, center_y - box_h // 2 + 20, box_w, box_h)
                pygame.draw.rect(surf, (0, 0, 0), box)
                pygame.draw.rect(surf, theme.ACCENT_DIM, box, 2)
                
                # Icon or placeholder in the middle
                icon = self.play_font.render("▶", True, theme.ACCENT)
                surf.blit(icon, (center_x - icon.get_width() // 2, center_y - icon.get_height() // 2 + 20))
                
                # Instructions
                hint = self.hint_font.render("Press A to Stop, B to Exit", True, theme.FG_DIM)
                surf.blit(hint, (center_x - hint.get_width() // 2, box.bottom + 20))
                
            else:
                # List View
                list_rect = pygame.Rect(
                    theme.PADDING,
                    head_h + theme.PADDING,
                    theme.SCREEN_W - 2 * theme.PADDING,
                    theme.SCREEN_H - head_h - 2 * theme.PADDING - 40
                )
                self.list.render(surf, list_rect, self.body_font)
                
                # Bottom help
                hint = self.hint_font.render("UP/DOWN: Navigate  A: Play  B: Back", True, theme.FG_DIM)
                surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
        except Exception as e:
            print(f"[media] Render error: {e}")
