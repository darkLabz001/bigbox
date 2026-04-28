"""Update View — High-fidelity tactical progress screen for OTA updates."""
from __future__ import annotations

import re
import math
import time
import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.widgets import ResultView


class UpdateView(ResultView):
    def __init__(self, title: str, text: str = "") -> None:
        super().__init__(title, text)
        self.progress = 0.0
        self.target_progress = 0.0
        self.status_msg = "INITIALIZING UPLINK..."
        self.start_time = time.time()
        
        self.f_title = pygame.font.Font(None, 40)
        self.f_status = pygame.font.Font(None, 28)
        self.f_log = pygame.font.Font(None, 18)
        self.f_huge = pygame.font.Font(None, 80)
        
        self._grid_surf = self._create_grid_bg()

    def _create_grid_bg(self) -> pygame.Surface:
        s = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H))
        s.fill(theme.BG)
        for x in range(0, theme.SCREEN_W, 30):
            pygame.draw.line(s, (15, 15, 25), (x, 0), (x, theme.SCREEN_H))
        for y in range(0, theme.SCREEN_H, 30):
            pygame.draw.line(s, (15, 15, 25), (0, y), (theme.SCREEN_W, y))
        return s

    def append(self, text: str) -> None:
        super().append(text)
        
        # Parse progress markers: [PROGRESS: 50] or just PROGRESS: 50
        match = re.search(r"PROGRESS:\s*(\d+)", text)
        if match:
            try:
                self.target_progress = float(match.group(1)) / 100.0
            except ValueError:
                pass
        
        # Parse status messages: [STATUS: Fetching...]
        status_match = re.search(r"STATUS:\s*(.*)", text)
        if status_match:
            self.status_msg = status_match.group(1).strip().upper()

    def handle(self, ev: ButtonEvent) -> None:
        # Override to prevent exiting while updating unless it's done or failed
        if not ev.pressed:
            return
        if ev.button is Button.B:
            # Allow exit if progress is 100% or if there's an error
            if self.progress >= 0.99 or "ERROR" in self.status_msg:
                self.dismissed = True
        elif ev.button is Button.UP:
            self.scroll = max(0, self.scroll - 1)
        elif ev.button is Button.DOWN:
            self.scroll += 1
        elif ev.button is Button.LL:
            self.scroll = max(0, self.scroll - 10)
        elif ev.button is Button.RR:
            self.scroll += 10

    def render(self, surf: pygame.Surface) -> None:
        # Smooth progress interpolation
        if self.progress < self.target_progress:
            self.progress += (self.target_progress - self.progress) * 0.1
        if self.progress > 0.99:
            self.progress = 1.0

        # Background
        surf.blit(self._grid_surf, (0, 0))
        
        # Header
        head_h = 50
        pygame.draw.rect(surf, (10, 10, 15), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        title = self.f_title.render("SYSTEM OTA UPDATE", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))
        
        # Spinner / UI element in header
        t = time.time()
        spinner_chars = "|/-\\"
        spinner = spinner_chars[int(t * 10) % len(spinner_chars)]
        spin_surf = self.f_title.render(f"[{spinner}]", True, theme.ACCENT_DIM)
        surf.blit(spin_surf, (theme.SCREEN_W - spin_surf.get_width() - theme.PADDING, (head_h - spin_surf.get_height()) // 2))

        # Main Content Area
        center_x = theme.SCREEN_W // 2
        center_y = theme.SCREEN_H // 2 - 20

        # 1. Circular Progress Ring
        radius = 100
        pygame.draw.circle(surf, (30, 30, 40), (center_x, center_y), radius, 8)
        
        if self.progress > 0:
            angle = 360 * self.progress
            rect = pygame.Rect(center_x - radius, center_y - radius, radius * 2, radius * 2)
            # Pygame arc uses radians and is counter-clockwise
            start_angle = math.radians(90)
            end_angle = math.radians(90 - angle)
            # Handle Pygame arc angle constraints
            if angle > 0:
                # Draw the progress arc
                color = theme.ERR if "ERROR" in self.status_msg else theme.ACCENT
                # Simple approximation by drawing multiple arcs or using a polygon
                # Since pygame.draw.arc is thin and finicky, we'll draw a thick arc using lines
                steps = max(1, int(angle / 2))
                for i in range(steps):
                    a = math.radians(90 - (i * 2))
                    cx = center_x + math.cos(a) * radius
                    cy = center_y - math.sin(a) * radius
                    pygame.draw.circle(surf, color, (int(cx), int(cy)), 4)
        
        # 2. Percentage Text
        perc_text = f"{int(self.progress * 100)}%"
        perc_surf = self.f_huge.render(perc_text, True, theme.FG)
        surf.blit(perc_surf, (center_x - perc_surf.get_width() // 2, center_y - perc_surf.get_height() // 2))

        # 3. Status Message
        status_color = theme.ERR if "ERROR" in self.status_msg else theme.ACCENT
        status_surf = self.f_status.render(self.status_msg, True, status_color)
        surf.blit(status_surf, (center_x - status_surf.get_width() // 2, center_y + radius + 30))

        # 4. Progress Bar (linear, below circle)
        bar_w = 400
        bar_h = 10
        bar_x = center_x - bar_w // 2
        bar_y = center_y + radius + 70
        
        pygame.draw.rect(surf, (20, 20, 30), (bar_x, bar_y, bar_w, bar_h), border_radius=4)
        pygame.draw.rect(surf, theme.DIVIDER, (bar_x, bar_y, bar_w, bar_h), 1, border_radius=4)
        
        if self.progress > 0:
            fill_w = int((bar_w - 4) * self.progress)
            if fill_w > 0:
                pygame.draw.rect(surf, status_color, (bar_x + 2, bar_y + 2, fill_w, bar_h - 4), border_radius=2)

        # 5. Log Output (Left Side overlay)
        log_x = 20
        log_y = head_h + 20
        log_w = 260
        log_h = theme.SCREEN_H - head_h - 60
        
        # Dimmed background for logs
        log_bg = pygame.Surface((log_w, log_h), pygame.SRCALPHA)
        log_bg.fill((0, 0, 0, 150))
        surf.blit(log_bg, (log_x, log_y))
        pygame.draw.rect(surf, theme.DIVIDER, (log_x, log_y, log_w, log_h), 1)
        
        surf.blit(self.f_log.render("UPDATE LOG", True, theme.ACCENT_DIM), (log_x + 10, log_y + 5))
        pygame.draw.line(surf, theme.DIVIDER, (log_x, log_y + 25), (log_x + log_w, log_y + 25))
        
        # Render log lines
        if self.lines:
            line_h = self.f_log.get_linesize()
            visible_lines = (log_h - 30) // line_h
            
            # Auto-scroll if near bottom
            if self.scroll >= len(self.lines) - visible_lines - 2:
                self.scroll = max(0, len(self.lines) - visible_lines)
                
            for i in range(visible_lines):
                idx = self.scroll + i
                if idx >= len(self.lines):
                    break
                line_text = self.lines[idx]
                if len(line_text) > 35:
                    line_text = line_text[:32] + "..."
                
                color = theme.FG_DIM
                if "ERROR" in line_text or "fail" in line_text.lower():
                    color = theme.ERR
                elif "STATUS:" in line_text:
                    color = theme.ACCENT
                
                surf.blit(self.f_log.render(line_text, True, color), (log_x + 5, log_y + 30 + i * line_h))

        # 6. Footer Hint
        if self.progress >= 0.99 or "ERROR" in self.status_msg:
            hint = self.f_log.render("PRESS B TO EXIT", True, theme.FG)
            # Make it pulse
            alpha = int(127 + 128 * math.sin(t * 5))
            hint.set_alpha(alpha)
            surf.blit(hint, (theme.SCREEN_W - hint.get_width() - 20, theme.SCREEN_H - 30))
