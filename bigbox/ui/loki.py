"""Loki UI — Autonomous Network Companion (Themed)."""
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
        
        self.title_font = pygame.font.Font(None, 32)
        self.face_font = pygame.font.Font(None, 90)
        self.stat_font = pygame.font.Font(None, 20)
        self.val_font = pygame.font.Font(None, 36)
        self.quote_font = pygame.font.Font(None, 22)
        self.mono_font = pygame.font.Font(None, 18)

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.A:
            if self.engine.running: self.engine.stop()
            else: self.engine.start()
        elif ev.button is Button.UP:
            self.scroll_y = max(0, self.scroll_y - 1)
        elif ev.button is Button.DOWN:
            self.scroll_y += 1

    def _get_face(self) -> str:
        m = self.engine.mood
        if m == "SCANNING": return "(o_o)"
        if m == "AGGRESSIVE": return "(>_<)"
        if m == "SUCCESS": return "(^v^)"
        if m == "ERROR": return "(x_x)"
        if m == "BORED": return "(-_-)"
        return "(^_^)"

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # 1. CHARACTER AREA (Left)
        char_rect = pygame.Rect(10, 10, 240, 160)
        pygame.draw.rect(surf, theme.BG_ALT, char_rect, border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT_DIM, char_rect, 2, border_radius=10)
        
        face_str = self._get_face()
        # Simple animation: slight bounce
        bounce = int(time.time() * 2) % 2 * 3 if self.engine.running else 0
        face_surf = self.face_font.render(face_str, True, theme.ACCENT)
        surf.blit(face_surf, (char_rect.centerx - face_surf.get_width()//2, 
                             char_rect.centery - face_surf.get_height()//2 + bounce))

        # 2. STATS GRID (Right) - 2x3 Grid like the real Loki
        grid_x, grid_y = 260, 10
        cell_w, cell_h = 170, 50
        
        stats_labels = [
            ("TARGETS", "targets"), ("PORTS", "ports"), ("VULNS", "vulns"),
            ("CREDS", "creds"), ("ZOMBIES", "zombies"), ("LOOT", "data")
        ]
        
        for i, (label, key) in enumerate(stats_labels):
            col, row = i % 3, i // 3
            x = grid_x + col * (cell_w + 10)
            y = grid_y + row * (cell_h + 10)
            
            # Cell Box
            r = pygame.Rect(x, y, cell_w, cell_h)
            pygame.draw.rect(surf, (15, 15, 20), r, border_radius=5)
            pygame.draw.rect(surf, theme.DIVIDER, r, 1, border_radius=5)
            
            # Label
            ls = self.stat_font.render(label, True, theme.FG_DIM)
            surf.blit(ls, (x + 8, y + 5))
            
            # Value
            val = str(self.engine.stats.get(key, 0))
            vs = self.val_font.render(val, True, theme.ACCENT if int(val) > 0 else theme.FG)
            surf.blit(vs, (x + 8, y + 18))

        # 3. COMMENTARY BOX (Middle)
        comm_rect = pygame.Rect(10, 180, theme.SCREEN_W - 20, 60)
        pygame.draw.rect(surf, (5, 5, 10), comm_rect, border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT_DIM, comm_rect, 1, border_radius=8)
        
        quote = f'"{self.engine.last_quote}"'
        qs = self.quote_font.render(quote, True, theme.FG)
        surf.blit(qs, (comm_rect.centerx - qs.get_width()//2, comm_rect.centery - qs.get_height()//2))

        # 4. HOST LIST (Bottom)
        list_rect = pygame.Rect(10, 250, theme.SCREEN_W - 20, 190)
        pygame.draw.rect(surf, (10, 10, 15), list_rect)
        pygame.draw.rect(surf, theme.DIVIDER, list_rect, 1)
        
        # List Header
        pygame.draw.rect(surf, theme.BG_ALT, (list_rect.x, list_rect.y, list_rect.width, 24))
        h1 = self.mono_font.render("IP ADDRESS", True, theme.ACCENT_DIM)
        h2 = self.mono_font.render("HOSTNAME", True, theme.ACCENT_DIM)
        h3 = self.mono_font.render("OPEN PORTS", True, theme.ACCENT_DIM)
        surf.blit(h1, (list_rect.x + 10, list_rect.y + 4))
        surf.blit(h2, (list_rect.x + 160, list_rect.y + 4))
        surf.blit(h3, (list_rect.x + 360, list_rect.y + 4))

        # Rows
        row_h = 22
        max_rows = (list_rect.height - 24) // row_h
        sorted_ips = sorted(self.engine.hosts.keys())
        
        for i in range(max_rows):
            idx = self.scroll_y + i
            if idx >= len(sorted_ips): break
            
            ip = sorted_ips[idx]
            h = self.engine.hosts[ip]
            y = list_rect.y + 28 + i * row_h
            
            color = theme.ACCENT if h.get("comp") else theme.FG_DIM
            
            t1 = self.mono_font.render(ip, True, color)
            t2 = self.mono_font.render(h.get("hostname", "Unknown")[:22], True, color)
            ports = ", ".join([p.split("/")[0] for p in h.get("ports", {}).keys()])
            t3 = self.mono_font.render(ports or "-", True, color)
            
            surf.blit(t1, (list_rect.x + 10, y))
            surf.blit(t2, (list_rect.x + 160, y))
            surf.blit(t3, (list_rect.x + 360, y))

        # 5. FOOTER
        status_bar = pygame.Rect(0, theme.SCREEN_H - 30, theme.SCREEN_W, 30)
        pygame.draw.rect(surf, theme.BG_ALT, status_bar)
        
        status_text = f"ENGINE: {self.engine.status} | TARGET: {self.engine.current_target or 'NONE'}"
        ss = self.mono_font.render(status_text, True, theme.ACCENT)
        surf.blit(ss, (theme.PADDING, status_bar.y + 7))
        
        hint = self.mono_font.render("A: TOGGLE  UP/DN: SCROLL  B: BACK", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - theme.PADDING, status_bar.y + 7))
