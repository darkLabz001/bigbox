"""Loki UI — Autonomous Network Companion (v2)."""
from __future__ import annotations

import time
import random
import pygame
from typing import TYPE_CHECKING, Optional

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.loki import LokiEngine

if TYPE_CHECKING:
    from bigbox.app import App


class LokiView:
    def __init__(self, engine: LokiEngine) -> None:
        self.engine = engine
        self.dismissed = False
        self.scroll_y = 0
        self.view_mode = "HOSTS" # HOSTS or LOG
        
        self.title_font = pygame.font.Font(None, 32)
        self.face_font = pygame.font.Font(None, 100)
        self.stat_font = pygame.font.Font(None, 20)
        self.val_font = pygame.font.Font(None, 36)
        self.quote_font = pygame.font.Font(None, 24)
        self.mono_font = pygame.font.Font(None, 18)
        
        self._last_anim = 0

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.A:
            if self.engine.running: self.engine.stop()
            else: self.engine.start()
        elif ev.button is Button.X:
            self.view_mode = "LOG" if self.view_mode == "HOSTS" else "HOSTS"
            self.scroll_y = 0
        elif ev.button is Button.UP:
            self.scroll_y = max(0, self.scroll_y - 1)
        elif ev.button is Button.DOWN:
            self.scroll_y += 1

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # 1. CHARACTER AREA (Left)
        # Update animation frame
        if time.time() - self._last_anim > 0.5:
            self.engine.update_animation()
            self._last_anim = time.time()

        char_rect = pygame.Rect(10, 10, 240, 160)
        pygame.draw.rect(surf, theme.BG_ALT, char_rect, border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT_DIM, char_rect, 2, border_radius=10)
        
        face_str = self.engine.get_face()
        face_surf = self.face_font.render(face_str, True, theme.ACCENT)
        surf.blit(face_surf, (char_rect.centerx - face_surf.get_width()//2, 
                             char_rect.centery - face_surf.get_height()//2))

        # 2. STATS GRID (Right)
        grid_x, grid_y = 260, 10
        cell_w, cell_h = 170, 50
        stats_labels = [
            ("TARGETS", "targets"), ("PORTS", "ports"), ("VULNS", "vulns"),
            ("CREDS", "creds"), ("ZOMBIES", "zombies"), ("DATA", "data")
        ]
        
        for i, (label, key) in enumerate(stats_labels):
            col, row = i % 3, i // 3
            r = pygame.Rect(grid_x + col * (cell_w + 10), grid_y + row * (cell_h + 10), cell_w, cell_h)
            pygame.draw.rect(surf, (15, 15, 20), r, border_radius=5)
            pygame.draw.rect(surf, theme.DIVIDER, r, 1, border_radius=5)
            surf.blit(self.stat_font.render(label, True, theme.FG_DIM), (r.x + 8, r.y + 5))
            val = str(self.engine.stats.get(key, 0))
            vs = self.val_font.render(val, True, theme.ACCENT if int(val) > 0 else theme.FG)
            surf.blit(vs, (r.x + 8, r.y + 18))

        # 3. COMMENTARY BOX
        comm_rect = pygame.Rect(10, 180, theme.SCREEN_W - 20, 50)
        pygame.draw.rect(surf, (5, 5, 10), comm_rect, border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT_DIM, comm_rect, 1, border_radius=8)
        
        # Show status as commentary if not idle
        status_text = f"[{self.engine.status}]" if self.engine.running else "IDLE"
        if self.engine.current_target:
            status_text += f" Target: {self.engine.current_target}"
            
        st_surf = self.quote_font.render(status_text, True, theme.FG)
        surf.blit(st_surf, (comm_rect.centerx - st_surf.get_width()//2, comm_rect.centery - st_surf.get_height()//2))

        # 4. DATA VIEW (Bottom)
        list_rect = pygame.Rect(10, 240, theme.SCREEN_W - 20, 200)
        pygame.draw.rect(surf, (10, 10, 15), list_rect)
        pygame.draw.rect(surf, theme.DIVIDER, list_rect, 1)
        
        # View Header
        pygame.draw.rect(surf, theme.BG_ALT, (list_rect.x, list_rect.y, list_rect.width, 24))
        mode_label = f"MODE: {self.view_mode}"
        surf.blit(self.mono_font.render(mode_label, True, theme.ACCENT), (list_rect.x + 10, list_rect.y + 4))

        if self.view_mode == "HOSTS":
            # List discovered hosts
            sorted_ips = sorted(self.engine.hosts.keys())
            row_h = 22
            max_rows = (list_rect.height - 24) // row_h
            for i in range(max_rows):
                idx = self.scroll_y + i
                if idx >= len(sorted_ips): break
                ip = sorted_ips[idx]
                h = self.engine.hosts[ip]
                y = list_rect.y + 28 + i * row_h
                color = theme.ACCENT if h.get("comp") else theme.FG_DIM
                line = f"{ip:<16} | {h.get('hostname','Unknown')[:20]:<20} | PORTS: {len(h.get('ports',{}))}"
                surf.blit(self.mono_font.render(line, True, color), (list_rect.x + 10, y))
        else:
            # List activity log
            row_h = 22
            max_rows = (list_rect.height - 24) // row_h
            for i in range(max_rows):
                idx = self.scroll_y + i
                if idx >= len(self.engine.event_log): break
                line = self.engine.event_log[idx]
                y = list_rect.y + 28 + i * row_h
                surf.blit(self.mono_font.render(line, True, theme.FG_DIM), (list_rect.x + 10, y))

        # 5. FOOTER
        status_bar = pygame.Rect(0, theme.SCREEN_H - 30, theme.SCREEN_W, 30)
        pygame.draw.rect(surf, theme.BG_ALT, status_bar)
        hint = self.mono_font.render("A: TOGGLE  X: VIEW MODE  UP/DN: SCROLL  B: BACK", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - theme.PADDING, status_bar.y + 7))
