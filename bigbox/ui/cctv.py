"""CCTV Viewer — High-performance MJPEG streaming based on KTOX patterns."""
from __future__ import annotations

import io
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import pygame
import requests
try:
    from turbojpeg import TurboJPEG
    _TJ = TurboJPEG()
except Exception:
    _TJ = None

from bigbox import theme
from bigbox.events import Button, ButtonEvent


@dataclass
class Camera:
    id: str
    location: str
    url: str
    ip: str = "UNKNOWN"
    is_stream: bool = True  # Try MJPEG streaming first


class CCTVView:
    """Full-screen CCTV monitoring using KTOX-style double buffering."""

    def __init__(self) -> None:
        # A mix of traffic cams and public infrastructure feeds.
        self.cameras = [
            # Traffic / Street Cams
            Camera("TRAF-SEA1", "Seattle: MLK & Jackson", "http://www.seattle.gov/trafficcams/images/MLK_S_Jackson_NS.jpg", "156.74.25.10", False),
            Camera("TRAF-SEA2", "Seattle: 4th & Battery", "http://www.seattle.gov/trafficcams/images/4_Battery_NS.jpg", "156.74.25.11", False),
            Camera("TRAF-WI", "Wisconsin: Cam 082", "https://projects.511wi.gov/milwaukee/cameras/cam082.jpg", "165.189.161.12", False),
            Camera("TRAF-UK", "UK: High Street", "http://194.168.163.96/axis-cgi/mjpg/video.cgi?resolution=320x240", "194.168.163.96", True),
            
            # Public Streams (MJPEG)
            Camera("CAM-UT", "Austin, TX (UT)", "http://porchcam.ece.utexas.edu/axis-cgi/mjpg/video.cgi?resolution=640x480", "128.83.120.20", True),
            Camera("CAM-CH", "Schaffhausen, CH", "http://87.245.83.189/axis-cgi/mjpg/video.cgi?resolution=640x480", "87.245.83.189", True),
            Camera("CAM-BER", "Berlin, DE", "http://213.218.26.109/stream.jpg", "213.218.26.109", True),
            Camera("CAM-PUR", "Purdue Univ", "http://webcam01.ecn.purdue.edu/mjpg/video.mjpg", "128.46.154.21", True),
            
            # Recon Mock
            Camera("RECON_MOCK", "Classified", "MOCK", "0.0.0.0", False),
        ]
        self.selected = 0
        self.dismissed = False
        
        self._frame_buffer = deque(maxlen=1)
        self.is_loading = True
        self.error_msg: str | None = None
        self.fps = 0
        
        self._noise_cache: list[pygame.Surface] = []
        self._generate_noise()
        
        self._stop_thread = False
        self._fetch_thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._fetch_thread.start()

    def _generate_noise(self) -> None:
        for _ in range(5):
            surf = pygame.Surface((320, 240))
            surf.fill((0, 0, 0))
            for _ in range(1200):
                surf.set_at((random.randint(0, 319), random.randint(0, 239)), (random.randint(40, 120),)*3)
            surf.set_alpha(70)
            self._noise_cache.append(surf)

    def _fetch_loop(self) -> None:
        """KTOX-optimized fetch loop with MJPEG/JPEG auto-detection and fallback."""
        while not self._stop_thread:
            cam = self.cameras[self.selected]
            current_idx = self.selected
            self.is_loading = True
            self.error_msg = None
            
            if cam.url == "MOCK":
                self.is_loading = False
                time.sleep(1)
                continue

            try:
                if cam.is_stream:
                    # MJPEG Stream Mode
                    with requests.get(cam.url, stream=True, timeout=8) as resp:
                        if resp.status_code != 200:
                            self.error_msg = f"HTTP {resp.status_code}"
                            time.sleep(3)
                            continue
                        
                        self.is_loading = False
                        bytes_buffer = bytes()
                        last_fps_check = time.time()
                        frames_this_sec = 0
                        
                        for chunk in resp.iter_content(chunk_size=32768):
                            if self._stop_thread or self.selected != current_idx:
                                break
                            
                            bytes_buffer += chunk
                            while True:
                                a = bytes_buffer.find(b'\xff\xd8') # SOI
                                b = bytes_buffer.find(b'\xff\xd9') # EOI
                                if a != -1 and b != -1 and b > a:
                                    jpg_data = bytes_buffer[a:b+2]
                                    bytes_buffer = bytes_buffer[b+2:]
                                    
                                    try:
                                        surf = pygame.image.load(io.BytesIO(jpg_data))
                                        self._frame_buffer.append(surf)
                                        frames_this_sec += 1
                                        
                                        now = time.time()
                                        if now - last_fps_check > 1.0:
                                            self.fps = frames_this_sec
                                            frames_this_sec = 0
                                            last_fps_check = now
                                    except Exception:
                                        pass
                                else:
                                    break
                            
                            if len(bytes_buffer) > 1024 * 1024:
                                bytes_buffer = bytes()
                else:
                    # Polling Mode (Static JPEGs)
                    while not self._stop_thread and self.selected == current_idx:
                        resp = requests.get(cam.url, timeout=5)
                        if resp.status_code == 200:
                            surf = pygame.image.load(io.BytesIO(resp.content))
                            self._frame_buffer.append(surf)
                            self.is_loading = False
                        else:
                            self.error_msg = f"HTTP {resp.status_code}"
                        
                        # Wait for refresh
                        start_poll = time.time()
                        while time.time() - start_poll < 2.0 and self.selected == current_idx:
                            time.sleep(0.1)
                            
            except Exception as e:
                self.error_msg = str(e)
                time.sleep(3)

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._stop_thread = True
            self.dismissed = True
        elif ev.button in (Button.UP, Button.DOWN):
            self.selected = (self.selected + (1 if ev.button is Button.DOWN else -1)) % len(self.cameras)
            self._frame_buffer.clear()
            self.fps = 0

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head = pygame.Rect(0, 0, theme.SCREEN_W, theme.STATUS_BAR_H + theme.TAB_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, head)
        title_font = pygame.font.Font(None, theme.FS_TITLE)
        title = title_font.render("RECON :: TRAFFIC_INTERCEPT", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head.height - title.get_height()) // 2))
        
        # LIVE indicator
        if int(time.time() * 2) % 2:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 140, head.height // 2), 6)
            rec_text = pygame.font.Font(None, theme.FS_SMALL).render("RECEIVING", True, theme.FG)
            surf.blit(rec_text, (theme.SCREEN_W - 125, (head.height - rec_text.get_height()) // 2))

        # Camera Selector
        list_w = 240
        for i, cam in enumerate(self.cameras):
            sel = i == self.selected
            y = head.bottom + 10 + i * 45
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, (0, y, list_w, 40))
                pygame.draw.line(surf, theme.ACCENT, (0, y), (0, y + 40), 3)
            color = theme.ACCENT if sel else theme.FG
            surf.blit(pygame.font.Font(None, theme.FS_BODY).render(cam.id, True, color), (20, y + 8))

        # Video Viewport
        view = pygame.Rect(list_w + 15, head.bottom + 15, theme.SCREEN_W - list_w - 30, theme.SCREEN_H - head.bottom - 30)
        pygame.draw.rect(surf, (0, 0, 0), view)
        pygame.draw.rect(surf, theme.DIVIDER, view, 1)

        # Content
        if self._frame_buffer:
            img = self._frame_buffer[0]
            try:
                scaled = pygame.transform.scale(img, (view.width, view.height))
                surf.blit(scaled, view.topleft)
                
                # Digital tint
                tint = pygame.Surface((view.width, view.height), pygame.SRCALPHA)
                tint.fill((0, 255, 0, 10)) 
                surf.blit(tint, view.topleft)
            except Exception:
                self._frame_buffer.clear()
        
        # UI Overlays
        f = pygame.font.Font(None, theme.FS_SMALL)
        if self.is_loading:
            msg = f.render("TUNING FREQUENCY...", True, theme.ACCENT)
            surf.blit(msg, (view.centerx - msg.get_width()//2, view.centery))
        elif self.error_msg and not self._frame_buffer:
            msg = f.render(f"LINK ERROR: {self.error_msg[:30]}", True, theme.ERR)
            surf.blit(msg, (view.centerx - msg.get_width()//2, view.centery))

        # OSD Details
        cam = self.cameras[self.selected]
        surf.blit(f.render(f"TARGET: {cam.ip} | SIGNAL: {self.fps} FPS", True, theme.ACCENT), (view.x + 8, view.bottom - 22))
        surf.blit(f.render(datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], True, theme.FG), (view.x + 8, view.y + 8))

        # Post-Processing (Scanlines + Noise)
        for y in range(view.y, view.bottom, 4):
            pygame.draw.line(surf, (0, 0, 0, 30), (view.x, y), (view.right, y))
        
        noise_surf = pygame.transform.scale(random.choice(self._noise_cache), (view.width, view.height))
        surf.blit(noise_surf, view.topleft)

        # Signal Glitch
        if random.random() < 0.04:
            gy = random.randint(view.y, view.bottom - 20)
            gh = random.randint(2, 10)
            if gy + gh <= view.bottom:
                glitch_rect = pygame.Rect(view.x, gy, view.width, gh)
                try:
                    sub = surf.subsurface(glitch_rect).copy()
                    surf.blit(sub, (view.x + random.randint(-5, 5), gy))
                except (ValueError, pygame.error): pass
