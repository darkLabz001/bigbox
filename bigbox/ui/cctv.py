"""CCTV Viewer — scrollable list of 'live' camera feeds with a glitchy/recon aesthetic."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent


@dataclass
class Camera:
    id: str
    location: str
    ip: str
    status: str = "ONLINE"


class CCTVView:
    """Full-screen CCTV monitoring interface."""

    def __init__(self) -> None:
        self.cameras = [
            Camera("CAM-01", "Front Entrance", "192.168.1.101"),
            Camera("CAM-02", "Server Room", "192.168.1.102"),
            Camera("CAM-03", "Loading Dock", "192.168.1.103"),
            Camera("CAM-04", "Parking Lot A", "192.168.1.104"),
            Camera("CAM-05", "Parking Lot B", "192.168.1.105"),
            Camera("CAM-06", "Roof Access", "192.168.1.106"),
            Camera("CAM-07", "Main Lobby", "192.168.1.107"),
            Camera("CAM-08", "Back Alley", "192.168.1.108"),
        ]
        self.selected = 0
        self.dismissed = False
        self.start_time = time.time()
        self._noise_cache: list[pygame.Surface] = []
        self._generate_noise()

    def _generate_noise(self) -> None:
        """Pre-generate some noise frames for performance."""
        for _ in range(5):
            surf = pygame.Surface((320, 240))
            surf.fill((0, 0, 0))
            for _ in range(1000):
                x = random.randint(0, 319)
                y = random.randint(0, 239)
                gray = random.randint(50, 150)
                surf.set_at((x, y), (gray, gray, gray))
            surf.set_alpha(60)
            self._noise_cache.append(surf)

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B and not ev.repeat:
            self.dismissed = True
        elif ev.button is Button.UP:
            self.selected = (self.selected - 1) % len(self.cameras)
        elif ev.button is Button.DOWN:
            self.selected = (self.selected + 1) % len(self.cameras)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Draw Header
        title_font = pygame.font.Font(None, theme.FS_TITLE)
        head = pygame.Rect(0, 0, theme.SCREEN_W, theme.STATUS_BAR_H + theme.TAB_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, head)
        pygame.draw.line(surf, theme.DIVIDER, (0, head.bottom - 1), (head.right, head.bottom - 1))
        
        title = title_font.render("RECON :: CCTV_VIEWER", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head.height - title.get_height()) // 2))
        
        # REC indicator
        if int(time.time() * 2) % 2 == 0:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 120, head.height // 2), 6)
            rec_font = pygame.font.Font(None, theme.FS_SMALL)
            rec_text = rec_font.render("REC", True, theme.FG)
            surf.blit(rec_text, (theme.SCREEN_W - 105, (head.height - rec_text.get_height()) // 2))

        # Camera List (Left Side)
        list_w = 250
        list_rect = pygame.Rect(0, head.bottom, list_w, theme.SCREEN_H - head.bottom)
        pygame.draw.rect(surf, theme.BG_ALT, list_rect)
        pygame.draw.line(surf, theme.DIVIDER, (list_rect.right, list_rect.y), (list_rect.right, list_rect.bottom))

        font = pygame.font.Font(None, theme.FS_BODY)
        small_font = pygame.font.Font(None, theme.FS_SMALL)
        
        for i, cam in enumerate(self.cameras):
            y = list_rect.y + 10 + i * 50
            sel = i == self.selected
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, (0, y - 5, list_w, 45))
                pygame.draw.line(surf, theme.ACCENT, (0, y - 5), (0, y + 40), 4)
            
            color = theme.ACCENT if sel else theme.FG
            name = font.render(cam.id, True, color)
            loc = small_font.render(cam.location, True, theme.FG_DIM)
            surf.blit(name, (20, y))
            surf.blit(loc, (20, y + 20))

        # Main View (Right Side)
        view_rect = pygame.Rect(list_w + 20, head.bottom + 20, theme.SCREEN_W - list_w - 40, theme.SCREEN_H - head.bottom - 40)
        pygame.draw.rect(surf, (0, 0, 0), view_rect)
        pygame.draw.rect(surf, theme.DIVIDER, view_rect, 2)
        
        # Current Camera Info
        cur = self.cameras[self.selected]
        info_text = font.render(f"{cur.id} - {cur.location}", True, theme.ACCENT)
        surf.blit(info_text, (view_rect.x, view_rect.y - 30))
        
        # Simulated "Video"
        # Draw some random shapes to look like a room
        random.seed(self.selected)
        for _ in range(5):
            rw = random.randint(50, 150)
            rh = random.randint(50, 150)
            rx = view_rect.x + random.randint(0, view_rect.width - rw)
            ry = view_rect.y + random.randint(0, view_rect.height - rh)
            pygame.draw.rect(surf, (20, 20, 30), (rx, ry, rw, rh))
            pygame.draw.rect(surf, (40, 40, 50), (rx, ry, rw, rh), 1)
        
        # Scanlines
        for y in range(view_rect.y, view_rect.bottom, 4):
            pygame.draw.line(surf, (0, 0, 0, 100), (view_rect.x, y), (view_rect.right, y))
            
        # Noise
        noise = random.choice(self._noise_cache)
        noise_scaled = pygame.transform.scale(noise, (view_rect.width, view_rect.height))
        surf.blit(noise_scaled, view_rect.topleft)
        
        # OSD
        osd_font = pygame.font.Font(None, theme.FS_SMALL)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ts_surf = osd_font.render(timestamp, True, (200, 200, 200))
        surf.blit(ts_surf, (view_rect.right - ts_surf.get_width() - 10, view_rect.bottom - 25))
        
        ip_surf = osd_font.render(f"IP: {cur.ip}", True, (200, 200, 200))
        surf.blit(ip_surf, (view_rect.x + 10, view_rect.bottom - 25))

        # Glitch effect occasionally
        if random.random() < 0.05:
            gy = random.randint(view_rect.y, view_rect.bottom - 10)
            gh = random.randint(2, 10)
            # Ensure glitch stays within bounds
            if gy + gh <= view_rect.bottom:
                glitch_rect = pygame.Rect(view_rect.x, gy, view_rect.width, gh)
                try:
                    sub = surf.subsurface(glitch_rect).copy()
                    surf.blit(sub, (view_rect.x + random.randint(-5, 5), gy))
                except ValueError:
                    pass
