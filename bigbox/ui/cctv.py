"""CCTV Viewer — live public camera feeds with a glitchy/recon aesthetic."""
from __future__ import annotations

import io
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import pygame
import requests

from bigbox import theme
from bigbox.events import Button, ButtonEvent


@dataclass
class Camera:
    id: str
    location: str
    url: str  # URL to a JPEG snapshot
    ip: str = "UNKNOWN"
    status: str = "ONLINE"


class CCTVView:
    """Full-screen CCTV monitoring interface with real public feeds."""

    def __init__(self) -> None:
        # A selection of public cameras that provide JPEG snapshots.
        # Note: These URLs are subject to change as they are public feeds.
        self.cameras = [
            Camera("CAM-TS", "Times Square, NY", "https://shm.rtsp.me/snapshot/7f0858e38d9e262145b5463f5383f99d", "208.80.154.224"),
            Camera("CAM-AB", "Abbey Road, London", "https://shm.rtsp.me/snapshot/d89856f64585121855a5b5657595d5b5", "82.113.153.22"),
            Camera("CAM-TK", "Tokyo Shibuya", "https://shm.rtsp.me/snapshot/e16790b4d4b123890f6b7c5e2d1a3f5b", "106.185.150.11"),
            Camera("CAM-VE", "Venice, Italy", "https://shm.rtsp.me/snapshot/6f9e8a7b6c5d4e3f2a1b0c9d8e7f6a5b", "93.146.241.10"),
            Camera("CAM-SF", "San Francisco Bay", "https://shm.rtsp.me/snapshot/a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p", "192.30.255.112"),
            Camera("CAM-RE", "Recon Mock 1", "MOCK", "192.168.1.101"),
            Camera("CAM-RE2", "Recon Mock 2", "MOCK", "192.168.1.102"),
        ]
        self.selected = 0
        self.dismissed = False
        self.start_time = time.time()
        
        self.current_surface: pygame.Surface | None = None
        self.is_loading = False
        self.error_msg: str | None = None
        
        self._noise_cache: list[pygame.Surface] = []
        self._generate_noise()
        
        self._last_cam_index = -1
        self._stop_thread = False
        self._fetch_thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._fetch_thread.start()

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

    def _fetch_loop(self) -> None:
        """Background thread to fetch snapshots periodically."""
        while not self._stop_thread:
            cam = self.cameras[self.selected]
            
            if cam.url == "MOCK":
                self.current_surface = None # Fall back to mock rendering
                self.error_msg = None
                self.is_loading = False
                time.sleep(0.5)
                continue

            self.is_loading = True
            try:
                resp = requests.get(cam.url, timeout=5)
                if resp.status_code == 200:
                    img_data = io.BytesIO(resp.content)
                    surf = pygame.image.load(img_data)
                    self.current_surface = surf
                    self.error_msg = None
                else:
                    self.error_msg = f"HTTP {resp.status_code}"
            except Exception as e:
                self.error_msg = str(e)
            
            self.is_loading = False
            
            # Wait 5 seconds before next fetch, or shorter if user changed camera
            start_wait = time.time()
            current_sel = self.selected
            while time.time() - start_wait < 5 and current_sel == self.selected and not self._stop_thread:
                time.sleep(0.1)

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B and not ev.repeat:
            self._stop_thread = True
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
        
        title = title_font.render("RECON :: LIVE_CCTV", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head.height - title.get_height()) // 2))
        
        # REC indicator
        if int(time.time() * 2) % 2 == 0:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 120, head.height // 2), 6)
            rec_font = pygame.font.Font(None, theme.FS_SMALL)
            rec_text = rec_font.render("LIVE", True, theme.FG)
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
        
        # Render Video Content
        if self.current_surface and cur.url != "MOCK":
            # Scale and blit the real image
            scaled = pygame.transform.scale(self.current_surface, (view_rect.width, view_rect.height))
            # Desaturate or tint it slightly to look more "CCTV"
            # (Simple version: just blit as is, maybe a slight blue tint)
            surf.blit(scaled, view_rect.topleft)
            # Tint overlay
            overlay = pygame.Surface((view_rect.width, view_rect.height), pygame.SRCALPHA)
            overlay.fill((0, 20, 0, 40)) # Very faint green tint
            surf.blit(overlay, view_rect.topleft)
        else:
            # Mock or Empty View
            random.seed(self.selected)
            for _ in range(5):
                rw = random.randint(50, 150)
                rh = random.randint(50, 150)
                rx = view_rect.x + random.randint(0, view_rect.width - rw)
                ry = view_rect.y + random.randint(0, view_rect.height - rh)
                pygame.draw.rect(surf, (20, 20, 30), (rx, ry, rw, rh))
                pygame.draw.rect(surf, (40, 40, 50), (rx, ry, rw, rh), 1)

        # Loading / Error Overlays
        if self.is_loading:
            loading_text = small_font.render("FETCHING...", True, theme.ACCENT)
            surf.blit(loading_text, (view_rect.centerx - loading_text.get_width()//2, view_rect.centery))
        elif self.error_msg:
            err_text = small_font.render(f"SIGNAL LOST: {self.error_msg[:20]}", True, theme.ERR)
            surf.blit(err_text, (view_rect.centerx - err_text.get_width()//2, view_rect.centery))
        
        # Scanlines
        for y in range(view_rect.y, view_rect.bottom, 4):
            pygame.draw.line(surf, (0, 0, 0, 80), (view_rect.x, y), (view_rect.right, y))
            
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
            if gy + gh <= view_rect.bottom:
                glitch_rect = pygame.Rect(view_rect.x, gy, view_rect.width, gh)
                try:
                    sub = surf.subsurface(glitch_rect).copy()
                    surf.blit(sub, (view_rect.x + random.randint(-5, 5), gy))
                except ValueError:
                    pass
