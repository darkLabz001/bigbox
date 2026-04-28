"""Loki UI — Autonomous Network Companion."""
from __future__ import annotations

import time
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
        self.cursor = 0
        self.scroll_y = 0
        
        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)
        self.mono_font = pygame.font.Font(None, 22)
        self.loki_font = pygame.font.Font(None, 80)

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.A:
            if self.engine.running:
                self.engine.stop()
            else:
                self.engine.start()
        elif ev.button is Button.UP:
            self.scroll_y = max(0, self.scroll_y - 1)
        elif ev.button is Button.DOWN:
            self.scroll_y += 1

    def _get_face(self) -> str:
        s = self.engine.status
        if "SCANNING" in s: return "(o_o)"
        if "PROBING" in s: return "( @ @ )"
        if "HYDRA" in s: return "(>_<)"
        if "ERROR" in s: return "(x_x)"
        if "SLEEPING" in s: return "(-_-)"
        return "(^_^)"

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        title = self.title_font.render("LOKI :: AUTONOMOUS COMPANION", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        # Loki Character Box
        loki_rect = pygame.Rect(theme.PADDING, head_h + theme.PADDING, 240, 200)
        pygame.draw.rect(surf, theme.BG_ALT, loki_rect, border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT_DIM, loki_rect, 2, border_radius=10)
        
        face_str = self._get_face()
        face_surf = self.loki_font.render(face_str, True, theme.ACCENT)
        surf.blit(face_surf, (loki_rect.centerx - face_surf.get_width()//2, loki_rect.centery - face_surf.get_height()//2 - 10))
        
        status_surf = self.body_font.render(self.engine.status, True, theme.FG)
        surf.blit(status_surf, (loki_rect.centerx - status_surf.get_width()//2, loki_rect.bottom - 40))

        # Stats / Targets Box
        stats_rect = pygame.Rect(loki_rect.right + theme.PADDING, loki_rect.y, 
                                 theme.SCREEN_W - loki_rect.width - 3*theme.PADDING, 200)
        pygame.draw.rect(surf, (5, 5, 10), stats_rect, border_radius=10)
        pygame.draw.rect(surf, theme.DIVIDER, stats_rect, 1, border_radius=10)
        
        # Target Info
        target_title = self.mono_font.render("CURRENT TARGET:", True, theme.ACCENT_DIM)
        surf.blit(target_title, (stats_rect.x + 15, stats_rect.y + 15))
        
        target_val = self.body_font.render(self.engine.current_target or "None", True, theme.FG)
        surf.blit(target_val, (stats_rect.x + 15, stats_rect.y + 40))
        
        hosts_found = self.mono_font.render(f"HOSTS DISCOVERED: {len(self.engine.hosts)}", True, theme.ACCENT_DIM)
        surf.blit(hosts_found, (stats_rect.x + 15, stats_rect.y + 80))
        
        # Hosts List (Bottom Area)
        list_rect = pygame.Rect(theme.PADDING, loki_rect.bottom + theme.PADDING, 
                                theme.SCREEN_W - 2*theme.PADDING, 
                                theme.SCREEN_H - loki_rect.bottom - 2*theme.PADDING - 30)
        pygame.draw.rect(surf, (10, 10, 15), list_rect)
        pygame.draw.rect(surf, theme.DIVIDER, list_rect, 1)
        
        # Render Host Rows
        row_h = 24
        max_rows = list_rect.height // row_h
        sorted_hosts = sorted(self.engine.hosts.keys())
        
        for i in range(max_rows):
            idx = self.scroll_y + i
            if idx >= len(sorted_hosts): break
            
            ip = sorted_hosts[idx]
            host_data = self.engine.hosts[ip]
            ports = ", ".join(host_data.get("ports", {}).keys()) or "none"
            
            line = f"{ip:<16} | PORTS: {ports[:50]}"
            line_surf = self.mono_font.render(line, True, theme.FG_DIM)
            surf.blit(line_surf, (list_rect.x + 10, list_rect.y + 5 + i*row_h))

        # Footer
        hint_text = "A: " + ("STOP" if self.engine.running else "START") + "  UP/DN: Scroll  B: Back"
        hint = self.mono_font.render(hint_text, True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 25))
