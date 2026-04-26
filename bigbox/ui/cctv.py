"""CCTV Viewer — High-performance MJPEG streaming based on KTOX patterns."""
from __future__ import annotations

import io
import random
import threading
import time
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import pygame
import requests
try:
    from turbojpeg import TurboJPEG, TJPF_RGB
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
    """Full-screen CCTV monitoring based on KTOX_Pi architecture."""

    def __init__(self) -> None:
        self.cameras = [
            Camera("CAM-01", "London, UK", "http://82.113.153.22/axis-cgi/mjpg/video.cgi"),
            Camera("CAM-02", "Schaffhausen, CH", "http://87.245.83.189/axis-cgi/mjpg/video.cgi?resolution=640x480"),
            Camera("CAM-03", "St. Malo, FR", "http://webcam.st-malo.com/axis-cgi/mjpg/video.cgi?resolution=640x480"),
            Camera("CAM-04", "Berlin, DE", "http://213.218.26.109/stream.jpg"),
            Camera("CAM-05", "Purdue, US", "http://webcam01.ecn.purdue.edu/mjpg/video.mjpg"),
            Camera("CAM-06", "Fair Harbor", "http://64.122.180.12/nphMotionJpeg?Resolution=640x480"),
        ]
        self.selected = 0
        self.dismissed = False
        
        # UI dimensions (800x480 screen)
        self.list_w = 220
        self.view_w = 540
        self.view_h = 380
        
        # State (matching KTOX)
        self._frame_buffer = deque(maxlen=1)
        self.is_loading = True
        self.error_msg: str | None = None
        self.fps = 0.0
        self.zoom = 1 # 1x, 2x, 4x
        self.grid_mode = False
        self._grid_frames = {} # index -> surface
        
        self._noise_cache: list[pygame.Surface] = []
        self._generate_noise()
        
        self._stop_thread = False
        self._fetch_thread = None
        self._start_stream_thread()

    def _generate_noise(self) -> None:
        for _ in range(3):
            surf = pygame.Surface((self.view_w, self.view_h))
            surf.fill((0, 0, 0))
            for _ in range(1500):
                surf.set_at((random.randint(0, self.view_w-1), random.randint(0, self.view_h-1)), (random.randint(10, 80),)*3)
            surf.set_alpha(60)
            self._noise_cache.append(surf)

    def _start_stream_thread(self):
        if self._fetch_thread and self._fetch_thread.is_alive():
            self._stop_thread = True
            self._fetch_thread.join(timeout=1.0)
        
        self._stop_thread = False
        self._fetch_thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._fetch_thread.start()

    def _fetch_loop(self) -> None:
        """KTOX-optimized fetch loop with 32KB chunks and TurboJPEG."""
        CHUNK_SIZE = 32768
        MAX_BUF = 1024 * 1024 # 1MB
        
        while not self._stop_thread:
            cam = self.cameras[self.selected]
            current_idx = self.selected
            self.is_loading = True
            self.error_msg = None
            
            try:
                with requests.get(cam.url, stream=True, timeout=10) as resp:
                    if resp.status_code != 200:
                        self.error_msg = f"HTTP {resp.status_code}"
                        time.sleep(2)
                        continue
                    
                    self.is_loading = False
                    buf = bytearray()
                    last_fps_check = time.time()
                    frames_this_sec = 0
                    
                    for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                        if self._stop_thread or self.selected != current_idx:
                            break
                        
                        buf.extend(chunk)
                        
                        while True:
                            a = buf.find(b'\xff\xd8') # SOI
                            b = buf.find(b'\xff\xd9', a + 2) # EOI
                            if a != -1 and b != -1:
                                jpg_data = bytes(buf[a:b+2])
                                del buf[:b+2]
                                
                                try:
                                    # Decode
                                    if _TJ:
                                        # (KTOX uses turbo.decode to raw, then image from array)
                                        # For simplicity in pygame, we use io.BytesIO
                                        raw_surf = pygame.image.load(io.BytesIO(jpg_data))
                                    else:
                                        raw_surf = pygame.image.load(io.BytesIO(jpg_data))
                                    
                                    # KTOX-style zoom/crop logic
                                    if self.zoom > 1:
                                        w, h = raw_surf.get_size()
                                        cw, ch = w // self.zoom, h // self.zoom
                                        cx, cy = (w - cw) // 2, (h - ch) // 2
                                        raw_surf = raw_surf.subsurface((cx, cy, cw, ch))
                                    
                                    # KTOX-style background scaling
                                    final_surf = pygame.transform.scale(raw_surf, (self.view_w, self.view_h))
                                    
                                    self._frame_buffer.append(final_surf)
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
                        
                        if len(buf) > MAX_BUF:
                            buf = bytearray()
                            
            except Exception as e:
                self.error_msg = str(e)
                time.sleep(2)

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._stop_thread = True
            self.dismissed = True
        elif ev.button in (Button.UP, Button.DOWN) and not ev.repeat:
            # Cycle Zoom
            if ev.button is Button.UP:
                self.zoom = 2 if self.zoom == 1 else (4 if self.zoom == 2 else 1)
            # Cycle Camera
            elif ev.button is Button.DOWN:
                self.selected = (self.selected + 1) % len(self.cameras)
                self._frame_buffer.clear()
                self.fps = 0.0
                self.zoom = 1
        elif ev.button in (Button.LEFT, Button.RIGHT) and not ev.repeat:
            # Prev Camera
            self.selected = (self.selected + (1 if ev.button is Button.RIGHT else -1)) % len(self.cameras)
            self._frame_buffer.clear()
            self.fps = 0.0
            self.zoom = 1

    def render(self, surf: pygame.Surface) -> None:
        # KTOX-style high-contrast theme (Black/White with Green accents)
        surf.fill((5, 5, 10)) # Darker than theme.BG
        
        head_h = 44
        head = pygame.Rect(0, 0, theme.SCREEN_W, head_h)
        pygame.draw.rect(surf, (10, 20, 30), head)
        pygame.draw.line(surf, theme.ACCENT, (0, head.bottom-1), (theme.SCREEN_W, head.bottom-1), 2)
        
        # Title
        title = pygame.font.Font(None, 32).render("CCTV :: INTERCEPT_V2", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))
        
        # Stats
        if int(time.time() * 2) % 2:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 160, head_h // 2), 6)
            msg = "LINK_ACTIVE"
        else:
            msg = "RECEIVING..."
        
        stat_text = pygame.font.Font(None, 24).render(msg, True, theme.FG)
        surf.blit(stat_text, (theme.SCREEN_W - 145, (head_h - stat_text.get_height()) // 2))

        # Camera Selector (Left)
        list_y = head.bottom + 10
        for i, cam in enumerate(self.cameras):
            sel = i == self.selected
            y = list_y + i * 50
            if sel:
                pygame.draw.rect(surf, (20, 40, 60), (0, y, self.list_w, 45))
                pygame.draw.line(surf, theme.ACCENT, (0, y), (0, y+45), 4)
            
            color = theme.ACCENT if sel else theme.FG_DIM
            name = pygame.font.Font(None, 28).render(cam.id, True, color)
            surf.blit(name, (20, y + 10))

        # Main Viewport (Right)
        view = pygame.Rect(self.list_w + 20, head.bottom + 20, self.view_w, self.view_h)
        pygame.draw.rect(surf, (0, 0, 0), view)
        pygame.draw.rect(surf, theme.ACCENT_DIM, view, 2)

        # Render Frame
        if self._frame_buffer:
            surf.blit(self._frame_buffer[0], view.topleft)
            
            # Post-Process: CRT Scanlines
            for y in range(view.y, view.bottom, 4):
                pygame.draw.line(surf, (0, 0, 0, 80), (view.x, y), (view.right, y))
        
        # Noise
        surf.blit(random.choice(self._noise_cache), view.topleft)

        # OSD Overlays
        f_small = pygame.font.Font(None, 22)
        cam = self.cameras[self.selected]
        
        # Target Info
        target_info = f_small.render(f"TARGET: {cam.location} | {cam.ip}", True, theme.ACCENT)
        surf.blit(target_info, (view.x + 10, view.y + 10))
        
        # System Stats
        fps_info = f_small.render(f"SIGNAL: {self.fps:.1f} FPS | ZOOM: {self.zoom}X", True, theme.FG)
        surf.blit(fps_info, (view.x + 10, view.bottom - 25))
        
        # Clock
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ts_surf = f_small.render(ts, True, theme.FG)
        surf.blit(ts_surf, (view.right - ts_surf.get_width() - 10, view.y + 10))

        # Loading / Error
        if self.is_loading:
            msg = f_small.render("SEARCHING FOR FREQUENCY...", True, theme.ACCENT)
            surf.blit(msg, (view.centerx - msg.get_width()//2, view.centery))
        elif self.error_msg and not self._frame_buffer:
            err = f_small.render(f"SIGNAL_LOST: {self.error_msg[:32]}", True, theme.ERR)
            surf.blit(err, (view.centerx - err.get_width()//2, view.centery))
        
        # Controls Hint
        hint = f_small.render("L/R: Switch Cam  UP: Zoom  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (view.x, view.bottom + 10))
