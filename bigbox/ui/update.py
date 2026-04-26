"""Update View — ResultView with a progress bar for OTA updates."""
from __future__ import annotations

import re
import pygame

from bigbox import theme
from bigbox.ui.widgets import ResultView


class UpdateView(ResultView):
    def __init__(self, title: str, text: str = "") -> None:
        super().__init__(title, text)
        self.progress = 0.0
        self.status_msg = "Starting..."

    def append(self, text: str) -> None:
        super().append(text)
        
        # Parse progress markers: [PROGRESS: 50] or just PROGRESS: 50
        match = re.search(r"PROGRESS:\s*(\d+)", text)
        if match:
            try:
                self.progress = float(match.group(1)) / 100.0
            except ValueError:
                pass
        
        # Parse status messages: [STATUS: Fetching...]
        status_match = re.search(r"STATUS:\s*(.*)", text)
        if status_match:
            self.status_msg = status_match.group(1).strip()

    def render(self, surf: pygame.Surface) -> None:
        # Render the base ResultView first
        super().render(surf)
        
        # Add progress bar at the bottom
        bar_h = 40
        bar_y = theme.SCREEN_H - bar_h
        
        # Background for progress area
        pygame.draw.rect(surf, theme.BG_ALT, (0, bar_y, theme.SCREEN_W, bar_h))
        pygame.draw.line(surf, theme.DIVIDER, (0, bar_y), (theme.SCREEN_W, bar_y), 2)
        
        # Progress bar track
        pad = 10
        track_w = theme.SCREEN_W - 2 * pad
        track_h = 10
        track_rect = pygame.Rect(pad, bar_y + 20, track_w, track_h)
        pygame.draw.rect(surf, theme.BG, track_rect)
        
        # Progress bar fill
        fill_w = int(track_w * max(0.0, min(1.0, self.progress)))
        if fill_w > 0:
            fill_rect = pygame.Rect(pad, bar_y + 20, fill_w, track_h)
            pygame.draw.rect(surf, theme.ACCENT, fill_rect)
            
        # Status text
        font = pygame.font.Font(None, theme.FS_SMALL)
        status = font.render(self.status_msg, True, theme.FG)
        surf.blit(status, (pad, bar_y + 4))
        
        # Percentage text
        perc = font.render(f"{int(self.progress * 100)}%", True, theme.ACCENT)
        surf.blit(perc, (theme.SCREEN_W - perc.get_width() - pad, bar_y + 4))
