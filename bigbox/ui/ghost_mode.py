"""Ghost Mode — Anti-Stalking Radar.

Identifies Bluetooth trackers (AirTags, etc.) that have followed the 
user across multiple GPS waypoints or days.
"""
from __future__ import annotations

import time
from datetime import datetime

import pygame

from bigbox import theme, tracker_history
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


class GhostModeView:
    def __init__(self) -> None:
        self.dismissed = False
        self.reports = tracker_history.analyse(min_score=2)
        self.cursor = 0
        self.scroll = 0
        self.last_refresh = time.time()

        self.f_title = pygame.font.Font(None, 36)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 18)

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self.dismissed = True
            return
        
        if not self.reports: return
        
        if ev.button is Button.UP:
            self.cursor = (self.cursor - 1) % len(self.reports)
        elif ev.button is Button.DOWN:
            self.cursor = (self.cursor + 1) % len(self.reports)
        elif ev.button is Button.X:
            # Refresh analysis
            self.reports = tracker_history.analyse(min_score=2)
            self.last_refresh = time.time()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill((10, 5, 5)) # Deep dark red background
        
        # Glow effect for "Ghost Mode" title
        pulse = 0.5 + 0.5 * abs((time.time() % 2.0) - 1.0)
        col = (int(255 * pulse), 50, 50)
        
        title = self.f_title.render("GHOST MODE :: STALKER RADAR", True, col)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2, 30))
        
        if not self.reports:
            msg = "NO PERSISTENT TRACKERS DETECTED"
            surf.blit(self.f_main.render(msg, True, theme.FG_DIM), (theme.SCREEN_W // 2 - 150, 200))
            return

        y = 80
        for i, r in enumerate(self.reports[:10]):
            sel = i == self.cursor
            rect = pygame.Rect(20, y, theme.SCREEN_W - 40, 40)
            if sel:
                pygame.draw.rect(surf, (60, 20, 20), rect, border_radius=4)
                pygame.draw.rect(surf, (255, 50, 50), rect, 1, border_radius=4)
            
            # Score indicator
            score_col = (255, 100, 100) if r.score > 5 else theme.FG
            score_surf = self.f_main.render(f"SCORE: {r.score}", True, score_col)
            surf.blit(score_surf, (30, y + 10))
            
            # MAC and Type
            ident = f"{r.address.upper()} ({r.type_key})"
            surf.blit(self.f_main.render(ident, True, theme.FG), (140, y + 10))
            
            # Stats
            stats = f"Seen: {r.sightings}x | Locs: {r.unique_locations} | Days: {r.unique_days}"
            surf.blit(self.f_small.render(stats, True, theme.FG_DIM), (400, y + 12))
            
            y += 45

        hint = self.f_small.render("X: REFRESH  B: BACK  |  Score = Locations * Days", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W // 2 - hint.get_width() // 2, theme.SCREEN_H - 30))
