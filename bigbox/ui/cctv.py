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
    url: str  # URL to a JPEG snapshot or MJPEG stream
    ip: str = "UNKNOWN"
    is_stream: bool = False


class CCTVView:
    """Full-screen CCTV monitoring interface with real public feeds."""

    def __init__(self) -> None:
        # A selection of public cameras. MJPEG streams provide "live" video.
        self.cameras = [
            Camera("CAM-UT", "Austin, TX (UT)", "http://porchcam.ece.utexas.edu/axis-cgi/mjpg/video.cgi?resolution=640x480", "128.83.120.20", True),
            Camera("CAM-CH", "Schaffhausen, CH", "http://87.245.83.189/axis-cgi/mjpg/video.cgi?resolution=640x480", "87.245.83.189", True),
            Camera("CAM-BER", "Berlin, Germany", "http://213.218.26.109/stream.jpg", "213.218.26.109", True),
            Camera("CAM-MAR", "Marina View", "http://webcam.fairharbormarina.com/nphMotionJpeg?Resolution=640x480", "64.122.180.12", True),
            Camera("CAM-MTN", "Stelvio Pass, Italy", "http://jpeg.popso.it/webcam/webcam_online/stelviolive_05.jpg", "80.82.17.40", False),
            Camera("CAM-WI", "Milwaukee Traffic", "https://projects.511wi.gov/milwaukee/cameras/cam082.jpg", "165.189.161.12", False),
        ]
        self.selected = 0
        self.dismissed = False
        self.start_time = time.time()
        
        self.current_surface: pygame.Surface | None = None
        self.is_loading = False
        self.error_msg: str | None = None
        self.fps = 0
        self._frame_count = 0
        self._fps_time = time.time()
        
        self._noise_cache: list[pygame.Surface] = []
        self._generate_noise()
        
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
        """Background thread to handle streaming and snapshots."""
        while not self._stop_thread:
            cam = self.cameras[self.selected]
            current_idx = self.selected
            self.is_loading = True
            self.current_surface = None
            
            try:
                if cam.is_stream:
                    # Handle MJPEG Stream
                    resp = requests.get(cam.url, stream=True, timeout=10)
                    if resp.status_code != 200:
                        self.error_msg = f"HTTP {resp.status_code}"
                        self.is_loading = False
                        time.sleep(2)
                        continue
                    
                    self.is_loading = False
                    self.error_msg = None
                    
                    bytes_buffer = bytes()
                    for chunk in resp.iter_content(chunk_size=4096):
                        if self._stop_thread or self.selected != current_idx:
                            break
                        
                        bytes_buffer += chunk
                        a = bytes_buffer.find(b'\xff\xd8') # JPEG Start
                        b = bytes_buffer.find(b'\xff\xd9') # JPEG End
                        
                        if a != -1 and b != -1:
                            jpg = bytes_buffer[a:b+2]
                            bytes_buffer = bytes_buffer[b+2:]
                            
                            try:
                                stream = io.BytesIO(jpg)
                                surf = pygame.image.load(stream)
                                self.current_surface = surf
                                self._frame_count += 1
                                
                                # Update FPS
                                now = time.time()
                                if now - self._fps_time > 1.0:
                                    self.fps = self._frame_count
                                    self._frame_count = 0
                                    self._fps_time = now
                                    
                            except Exception:
                                pass
                else:
                    # Handle Static Snapshots
                    self.error_msg = None
                    resp = requests.get(cam.url, timeout=10)
                    if resp.status_code == 200:
                        stream = io.BytesIO(resp.content)
                        self.current_surface = pygame.image.load(stream)
                    else:
                        self.error_msg = f"HTTP {resp.status_code}"
                    
                    self.is_loading = False
                    # Wait before next refresh for static cams
                    for _ in range(30): # 3 second wait
                        if self._stop_thread or self.selected != current_idx:
                            break
                        time.sleep(0.1)
                        
            except Exception as e:
                self.error_msg = str(e)
                self.is_loading = False
                time.sleep(2)

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
        
        # Header
        title_font = pygame.font.Font(None, theme.FS_TITLE)
        head = pygame.Rect(0, 0, theme.SCREEN_W, theme.STATUS_BAR_H + theme.TAB_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, head)
        pygame.draw.line(surf, theme.DIVIDER, (0, head.bottom - 1), (head.right, head.bottom - 1))
        
        title = title_font.render("RECON :: LIVE_INTERCEPT", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head.height - title.get_height()) // 2))
        
        # LIVE indicator
        if int(time.time() * 2) % 2 == 0:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 140, head.height // 2), 6)
            rec_font = pygame.font.Font(None, theme.FS_SMALL)
            rec_text = rec_font.render("LIVE FEED", True, theme.FG)
            surf.blit(rec_text, (theme.SCREEN_W - 125, (head.height - rec_text.get_height()) // 2))

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
        if self.current_surface:
            try:
                scaled = pygame.transform.scale(self.current_surface, (view_rect.width, view_rect.height))
                surf.blit(scaled, view_rect.topleft)
                
                # Digital tint (subtle)
                tint = pygame.Surface((view_rect.width, view_rect.height), pygame.SRCALPHA)
                tint.fill((0, 30, 0, 20)) 
                surf.blit(tint, view_rect.topleft)
            except Exception:
                self.current_surface = None
        else:
            # Signal Search / Empty
            random.seed(int(time.time() * 5))
            for _ in range(3):
                rw = random.randint(100, 300)
                rh = random.randint(2, 20)
                rx = view_rect.x + random.randint(-50, view_rect.width)
                ry = view_rect.y + random.randint(0, view_rect.height)
                pygame.draw.rect(surf, (20, 40, 30), (rx, ry, rw, rh))

        # Status Overlays
        if self.is_loading:
            loading_text = small_font.render("SEARCHING FOR SIGNAL...", True, theme.ACCENT)
            surf.blit(loading_text, (view_rect.centerx - loading_text.get_width()//2, view_rect.centery))
        elif self.error_msg and not self.current_surface:
            err_text = small_font.render(f"ENCRYPTION ERROR: {self.error_msg[:30]}", True, theme.ERR)
            surf.blit(err_text, (view_rect.centerx - err_text.get_width()//2, view_rect.centery))
        
        # Signal Strength Bars
        if self.current_surface:
            strength = random.randint(3, 5) if random.random() > 0.1 else 1
            for i in range(5):
                color = theme.ACCENT if i < strength else theme.DIVIDER
                pygame.draw.rect(surf, color, (view_rect.right - 60 + i*10, view_rect.y + 10, 6, 12))

        # OSD / Stats
        osd_font = pygame.font.Font(None, theme.FS_SMALL)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        ts_surf = osd_font.render(f"DATE: {timestamp}", True, (220, 220, 220))
        surf.blit(ts_surf, (view_rect.x + 10, view_rect.y + 10))
        
        ip_surf = osd_font.render(f"TARGET: {cur.ip} | {self.fps} FPS", True, theme.ACCENT)
        surf.blit(ip_surf, (view_rect.x + 10, view_rect.bottom - 25))

        # Scanlines & Noise
        for y in range(view_rect.y, view_rect.bottom, 4):
            pygame.draw.line(surf, (0, 0, 0, 60), (view_rect.x, y), (view_rect.right, y))
            
        noise = random.choice(self._noise_cache)
        noise_scaled = pygame.transform.scale(noise, (view_rect.width, view_rect.height))
        surf.blit(noise_scaled, view_rect.topleft)

        # Intermittent Glitch
        if random.random() < 0.04:
            gy = random.randint(view_rect.y, view_rect.bottom - 20)
            gh = random.randint(5, 15)
            if gy + gh <= view_rect.bottom:
                glitch_rect = pygame.Rect(view_rect.x, gy, view_rect.width, gh)
                try:
                    sub = surf.subsurface(glitch_rect).copy()
                    surf.blit(sub, (view_rect.x + random.randint(-10, 10), gy))
                except ValueError: pass
