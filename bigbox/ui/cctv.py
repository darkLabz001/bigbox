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


class CCTVView:
    """Full-screen CCTV monitoring using KTOX-style double buffering."""

    def __init__(self) -> None:
        self.cameras = [
            Camera("CAM-UT", "Austin, TX", "http://porchcam.ece.utexas.edu/axis-cgi/mjpg/video.cgi?resolution=640x480"),
            Camera("CAM-CH", "Schaffhausen, CH", "http://87.245.83.189/axis-cgi/mjpg/video.cgi?resolution=640x480"),
            Camera("CAM-BER", "Berlin, DE", "http://213.218.26.109/stream.jpg"),
            Camera("CAM-MAR", "Marina South", "http://webcam.fairharbormarina.com/nphMotionJpeg?Resolution=640x480"),
            Camera("CAM-ST", "Stelvio Pass", "http://jpeg.popso.it/webcam/webcam_online/stelviolive_05.jpg"),
        ]
        self.selected = 0
        self.dismissed = False
        
        # Double buffering: fetch loop pushes to deque, render loop pops
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
            for _ in range(1000):
                surf.set_at((random.randint(0, 319), random.randint(0, 239)), (random.randint(50, 150),)*3)
            surf.set_alpha(60)
            self._noise_cache.append(surf)

    def _fetch_loop(self) -> None:
        """KTOX-optimized fetch loop with MJPEG/JPEG auto-detection."""
        while not self._stop_thread:
            cam = self.cameras[self.selected]
            current_idx = self.selected
            self.is_loading = True
            self.error_msg = None
            
            try:
                # 1. Detect stream type
                with requests.get(cam.url, stream=True, timeout=8) as resp:
                    if resp.status_code != 200:
                        self.error_msg = f"HTTP {resp.status_code}"
                        time.sleep(2)
                        continue
                    
                    ctype = resp.headers.get('Content-Type', '').lower()
                    self.is_loading = False
                    
                    if 'multipart/x-mixed-replace' in ctype or 'mjpeg' in cam.url.lower():
                        # MJPEG Streaming Mode (Byte-level parser)
                        bytes_buffer = bytes()
                        last_fps_check = time.time()
                        frames_this_sec = 0
                        
                        for chunk in resp.iter_content(chunk_size=32768): # 32KB chunks (KTOX)
                            if self._stop_thread or self.selected != current_idx:
                                break
                            
                            bytes_buffer += chunk
                            while True:
                                a = bytes_buffer.find(b'\xff\xd8')
                                b = bytes_buffer.find(b'\xff\xd9')
                                if a != -1 and b != -1:
                                    jpg_data = bytes_buffer[a:b+2]
                                    bytes_buffer = bytes_buffer[b+2:]
                                    
                                    # Decode using TurboJPEG if available
                                    try:
                                        if _TJ:
                                            # TurboJPEG to raw, then to pygame
                                            raw = _TJ.decode(jpg_data, pixel_format=0) # 0 = RGB
                                            # We need to know dimensions to build surface; fallback to PIL
                                            surf = pygame.image.load(io.BytesIO(jpg_data))
                                        else:
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
                            
                            if len(bytes_buffer) > 524288: # 512KB Safety Cap
                                bytes_buffer = bytes()
                    else:
                        # Polling Mode (Static JPEGs)
                        while not self._stop_thread and self.selected == current_idx:
                            r = requests.get(cam.url, timeout=5)
                            if r.status_code == 200:
                                surf = pygame.image.load(io.BytesIO(r.content))
                                self._frame_buffer.append(surf)
                            time.sleep(1.0) # Poll at 1Hz
                            
            except Exception as e:
                self.error_msg = str(e)
                time.sleep(2)

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._stop_thread = True
            self.dismissed = True
        elif ev.button in (Button.UP, Button.DOWN):
            self.selected = (self.selected + (1 if ev.button is Button.DOWN else -1)) % len(self.cameras)
            self._frame_buffer.clear()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head = pygame.Rect(0, 0, theme.SCREEN_W, theme.STATUS_BAR_H + theme.TAB_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, head)
        title = pygame.font.Font(None, theme.FS_TITLE).render("RECON :: STREAM_INTERCEPT", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head.height - title.get_height()) // 2))
        
        # Live Dot
        if int(time.time() * 2) % 2:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 140, head.height // 2), 6)

        # List
        list_w = 240
        for i, cam in enumerate(self.cameras):
            sel = i == self.selected
            y = head.bottom + 10 + i * 45
            if sel: pygame.draw.rect(surf, theme.SELECTION_BG, (0, y, list_w, 40))
            color = theme.ACCENT if sel else theme.FG
            surf.blit(pygame.font.Font(None, theme.FS_BODY).render(cam.id, True, color), (20, y + 8))

        # Viewport
        view = pygame.Rect(list_w + 15, head.bottom + 15, theme.SCREEN_W - list_w - 30, theme.SCREEN_H - head.bottom - 30)
        pygame.draw.rect(surf, (0, 0, 0), view)
        pygame.draw.rect(surf, theme.DIVIDER, view, 2)

        # Draw Frame
        if self._frame_buffer:
            img = self._frame_buffer[0]
            surf.blit(pygame.transform.scale(img, (view.width, view.height)), view.topleft)
        
        # Overlays
        f = pygame.font.Font(None, theme.FS_SMALL)
        if self.is_loading:
            msg = f.render("TUNING FREQUENCY...", True, theme.ACCENT)
            surf.blit(msg, (view.centerx - msg.get_width()//2, view.centery))
        elif self.error_msg and not self._frame_buffer:
            msg = f.render(f"SIGNAL LOSS: {self.error_msg[:25]}", True, theme.ERR)
            surf.blit(msg, (view.centerx - msg.get_width()//2, view.centery))

        # OSD
        cam = self.cameras[self.selected]
        surf.blit(f.render(f"TARGET: {cam.ip} | {self.fps} FPS", True, theme.ACCENT), (view.x + 5, view.bottom - 20))
        surf.blit(f.render(datetime.now().strftime("%H:%M:%S.%f")[:-3], True, theme.FG), (view.right - 90, view.y + 5))

        # Post-process (scanlines + noise)
        for y in range(view.y, view.bottom, 4):
            pygame.draw.line(surf, (0, 0, 0, 50), (view.x, y), (view.right, y))
        surf.blit(pygame.transform.scale(random.choice(self._noise_cache), (view.width, view.height)), view.topleft)
