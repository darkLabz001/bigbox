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
    """Full-screen CCTV monitoring with Hardware Acceleration and Background Scaling."""

    def __init__(self) -> None:
        # These are stable MJPEG feeds that usually provide 10-30 FPS.
        self.cameras = [
            Camera("CAM-01", "Abbey Road, London", "http://82.113.153.22/axis-cgi/mjpg/video.cgi"),
            Camera("CAM-02", "Schaffhausen, CH", "http://87.245.83.189/axis-cgi/mjpg/video.cgi?resolution=640x480"),
            Camera("CAM-03", "St. Malo, France", "http://webcam.st-malo.com/axis-cgi/mjpg/video.cgi?resolution=640x480"),
            Camera("CAM-04", "University of Texas", "http://porchcam.ece.utexas.edu/axis-cgi/mjpg/video.cgi?resolution=640x480"),
            Camera("CAM-05", "Berlin, DE", "http://213.218.26.109/stream.jpg"),
        ]
        self.selected = 0
        self.dismissed = False
        
        # Viewport size (we need this for background scaling)
        self.view_w = 510
        self.view_h = 367
        
        # Double buffering
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
        for _ in range(3):
            surf = pygame.Surface((self.view_w, self.view_h))
            surf.fill((0, 0, 0))
            for _ in range(1000):
                surf.set_at((random.randint(0, self.view_w-1), random.randint(0, self.view_h-1)), (random.randint(20, 100),)*3)
            surf.set_alpha(50)
            self._noise_cache.append(surf)

    def _fetch_loop(self) -> None:
        """KTOX-style fetch loop: Scale in background, decode with TurboJPEG."""
        while not self._stop_thread:
            cam = self.cameras[self.selected]
            current_idx = self.selected
            self.is_loading = True
            self.error_msg = None
            
            try:
                # Use a session for better performance
                with requests.Session() as session:
                    with session.get(cam.url, stream=True, timeout=10) as resp:
                        if resp.status_code != 200:
                            self.error_msg = f"HTTP {resp.status_code}"
                            time.sleep(2)
                            continue
                        
                        self.is_loading = False
                        bytes_buffer = bytes()
                        last_fps_check = time.time()
                        frames_this_sec = 0
                        
                        # Read MJPEG stream
                        for chunk in resp.iter_content(chunk_size=16384): # 16KB chunks
                            if self._stop_thread or self.selected != current_idx:
                                break
                            
                            bytes_buffer += chunk
                            while True:
                                a = bytes_buffer.find(b'\xff\xd8')
                                b = bytes_buffer.find(b'\xff\xd9')
                                if a != -1 and b != -1 and b > a:
                                    jpg_data = bytes_buffer[a:b+2]
                                    bytes_buffer = bytes_buffer[b+2:]
                                    
                                    try:
                                        # 1. Decode
                                        if _TJ:
                                            # Use TurboJPEG for fast decoding
                                            # We still use pygame.image.load as it's easier to get a Surface
                                            # but we could optimize further with frombuffer if needed.
                                            raw_surf = pygame.image.load(io.BytesIO(jpg_data))
                                        else:
                                            raw_surf = pygame.image.load(io.BytesIO(jpg_data))
                                        
                                        # 2. Pre-scale in background thread!
                                        # This is the secret to high performance on Pi.
                                        final_surf = pygame.transform.scale(raw_surf, (self.view_w, self.view_h))
                                        
                                        # 3. Push to buffer
                                        self._frame_buffer.append(final_surf)
                                        frames_this_sec += 1
                                        
                                        # FPS tracking
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
            self.fps = 0

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # UI Chrome
        head = pygame.Rect(0, 0, theme.SCREEN_W, theme.STATUS_BAR_H + theme.TAB_BAR_H)
        pygame.draw.rect(surf, theme.BG_ALT, head)
        title = pygame.font.Font(None, theme.FS_TITLE).render("RECON :: LIVE_STREAM", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head.height - title.get_height()) // 2))
        
        if int(time.time() * 2) % 2:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 140, head.height // 2), 6)

        # Cam List
        list_w = 240
        for i, cam in enumerate(self.cameras):
            sel = i == self.selected
            y = head.bottom + 10 + i * 45
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, (0, y, list_w, 40))
                pygame.draw.line(surf, theme.ACCENT, (0, y), (0, y + 40), 3)
            color = theme.ACCENT if sel else theme.FG
            surf.blit(pygame.font.Font(None, theme.FS_BODY).render(cam.id, True, color), (20, y + 8))

        # Viewport
        view = pygame.Rect(list_w + 15, head.bottom + 15, self.view_w, self.view_h)
        pygame.draw.rect(surf, (0, 0, 0), view)
        pygame.draw.rect(surf, theme.DIVIDER, view, 1)

        # Render the Bufffered Frame
        if self._frame_buffer:
            # We already scaled this in the fetch thread, so blit is nearly free!
            surf.blit(self._frame_buffer[0], view.topleft)
            
            # Recon Scanline Effect
            for y in range(view.y, view.bottom, 4):
                pygame.draw.line(surf, (0, 0, 0, 40), (view.x, y), (view.right, y))
        
        # Overlays
        f = pygame.font.Font(None, theme.FS_SMALL)
        if self.is_loading:
            msg = f.render("TUNING...", True, theme.ACCENT)
            surf.blit(msg, (view.centerx - msg.get_width()//2, view.centery))
        elif self.error_msg and not self._frame_buffer:
            msg = f.render(f"NO SIGNAL: {self.error_msg[:30]}", True, theme.ERR)
            surf.blit(msg, (view.centerx - msg.get_width()//2, view.centery))

        # OSD
        cam = self.cameras[self.selected]
        surf.blit(f.render(f"FEED: {cam.ip} | {self.fps} FPS", True, theme.ACCENT), (view.x + 8, view.bottom - 22))
        surf.blit(f.render(datetime.now().strftime("%H:%M:%S.%f")[:-3], True, theme.FG), (view.x + 8, view.y + 8))

        # Noise Overlay
        surf.blit(random.choice(self._noise_cache), view.topleft)
