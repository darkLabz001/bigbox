"""CCTV Viewer — High-performance MJPEG streaming engine."""
from __future__ import annotations

import io
import random
import threading
import time
import re
import os
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
    """Full-screen CCTV monitoring based on optimized MJPEG architecture."""

    def __init__(self) -> None:
        # Verified WORKING URLs (as of this minute)
        self.cameras = [
            Camera("AVALON", "Avalon Golf", "http://74.95.172.65:8100/axis-cgi/mjpg/video.cgi"),
            Camera("NORWAY", "Norway Coast", "http://78.31.82.246/mjpg/video.mjpg"),
            Camera("PLAYA", "Playa Levante", "http://212.170.100.189/mjpg/video.mjpg"),
            Camera("PRESCOTT", "Airport USA", "http://199.104.253.4/mjpg/video.mjpg"),
            Camera("MADRID", "Madrid, ESP", "http://83.48.75.113:8320/axis-cgi/mjpg/video.cgi"),
            Camera("STELVIO", "Stelvio Pass", "https://jpeg.popso.it/webcam/webcam_online/stelviolive_05.jpg"),
            Camera("MARINA", "Fair Harbor", "http://webcam.fairharbormarina.com/nphMotionJpeg?Resolution=640x480"),
            Camera("SEATTLE1", "MLK & Jackson", "http://www.seattle.gov/trafficcams/images/MLK_S_Jackson_NS.jpg"),
            Camera("SEATTLE2", "4th & Battery", "http://www.seattle.gov/trafficcams/images/4_Battery_NS.jpg"),
        ]
        
        self._load_manual_urls()
        self.selected = 0
        self.dismissed = False
        
        # UI dimensions
        self.list_w = 220
        self.view_w = 540
        self.view_h = 380
        
        # State
        self._frame_buffer = deque(maxlen=1)
        self.is_loading = True
        self.error_msg: str | None = None
        self.fps = 0.0
        self.zoom = 1
        
        self._noise_cache: list[pygame.Surface] = []
        self._generate_noise()
        
        self._stop_thread = False
        self._fetch_thread = None
        self._start_stream_thread()

    def _load_manual_urls(self):
        manual_path = "/opt/bigbox/config/manual_urls.txt"
        if os.path.exists(manual_path):
            try:
                with open(manual_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"): continue
                        parts = [p.strip() for p in line.split("|")]
                        if len(parts) >= 2:
                            self.cameras.insert(0, Camera(parts[0][:12], parts[0], parts[1]))
            except Exception: pass

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
        CHUNK_SIZE = 32768
        MAX_BUF = 1024 * 1024
        
        while not self._stop_thread:
            cam = self.cameras[self.selected]
            current_idx = self.selected
            self.is_loading = True
            self.error_msg = None
            
            try:
                ip_match = re.search(r'://([^:/]+)', cam.url)
                cam_ip = ip_match.group(1) if ip_match else "UNKNOWN"
                
                # Check stream type with redirects followed
                content_type = ""
                try:
                    r = requests.get(cam.url, timeout=5, stream=True, allow_redirects=True)
                    content_type = r.headers.get("Content-Type", "").lower()
                    # We keep the response object if it's MJPEG
                    resp = r
                except Exception as e:
                    self.error_msg = str(e)
                    time.sleep(2)
                    continue

                if "multipart" in content_type or "mjpeg" in cam.url.lower():
                    # --- MJPEG STREAMING MODE ---
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
                                    raw_surf = pygame.image.load(io.BytesIO(jpg_data))
                                    if self.zoom > 1:
                                        w, h = raw_surf.get_size()
                                        cw, ch = w // self.zoom, h // self.zoom
                                        cx, cy = (w - cw) // 2, (h - ch) // 2
                                        raw_surf = raw_surf.subsurface((cx, cy, cw, ch))
                                    
                                    final_surf = pygame.transform.scale(raw_surf, (self.view_w, self.view_h))
                                    self._frame_buffer.append((final_surf, cam_ip))
                                    frames_this_sec += 1
                                    now = time.time()
                                    if now - last_fps_check > 1.0:
                                        self.fps = frames_this_sec
                                        frames_this_sec = 0
                                        last_fps_check = now
                                except Exception: pass
                            else:
                                break
                        if len(buf) > MAX_BUF: buf = bytearray()
                    resp.close()
                else:
                    # --- SINGLE SNAPSHOT POLLING MODE ---
                    # The response we already got might have the image data
                    if resp.status_code == 200:
                        try:
                            raw_surf = pygame.image.load(io.BytesIO(resp.content))
                            final_surf = pygame.transform.scale(raw_surf, (self.view_w, self.view_h))
                            self._frame_buffer.append((final_surf, cam_ip))
                            self.is_loading = False
                            self.fps = 1.0
                        except Exception: pass
                    resp.close()

                    while not self._stop_thread and self.selected == current_idx:
                        resp = requests.get(cam.url, timeout=5, allow_redirects=True)
                        if resp.status_code == 200:
                            raw_surf = pygame.image.load(io.BytesIO(resp.content))
                            final_surf = pygame.transform.scale(raw_surf, (self.view_w, self.view_h))
                            self._frame_buffer.append((final_surf, cam_ip))
                            self.is_loading = False
                            self.fps = 1.0
                        else:
                            self.error_msg = f"HTTP {resp.status_code}"
                        
                        poll_start = time.time()
                        while time.time() - poll_start < 2.0 and self.selected == current_idx:
                            time.sleep(0.1)

            except Exception as e:
                self.error_msg = str(e)
                time.sleep(2)

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._stop_thread = True
            self.dismissed = True
        elif ev.button in (Button.UP, Button.DOWN) and not ev.repeat:
            if ev.button is Button.UP:
                self.zoom = 2 if self.zoom == 1 else (4 if self.zoom == 2 else 1)
            elif ev.button is Button.DOWN:
                self.selected = (self.selected + 1) % len(self.cameras)
                self._frame_buffer.clear()
                self.fps = 0.0
                self.zoom = 1
        elif ev.button in (Button.LEFT, Button.RIGHT) and not ev.repeat:
            self.selected = (self.selected + (1 if ev.button is Button.RIGHT else -1)) % len(self.cameras)
            self._frame_buffer.clear()
            self.fps = 0.0
            self.zoom = 1

    def render(self, surf: pygame.Surface) -> None:
        surf.fill((5, 5, 10)) 
        
        head_h = 44
        head = pygame.Rect(0, 0, theme.SCREEN_W, head_h)
        pygame.draw.rect(surf, (10, 20, 30), head)
        pygame.draw.line(surf, theme.ACCENT, (0, head.bottom-1), (theme.SCREEN_W, head.bottom-1), 2)
        
        title = pygame.font.Font(None, 32).render("CCTV :: LIVE_INTERCEPT", True, theme.ACCENT)

        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))
        
        if int(time.time() * 2) % 2:
            pygame.draw.circle(surf, theme.ERR, (theme.SCREEN_W - 160, head_h // 2), 6)
            msg = "LINK_ACTIVE"
        else:
            msg = "TUNING..."
        
        stat_text = pygame.font.Font(None, 24).render(msg, True, theme.FG)
        surf.blit(stat_text, (theme.SCREEN_W - 145, (head_h - stat_text.get_height()) // 2))

        # List
        list_y = head.bottom + 10
        for i, cam in enumerate(self.cameras):
            sel = i == self.selected
            y = list_y + i * 36
            if y > theme.SCREEN_H - 40: break
            if sel:
                pygame.draw.rect(surf, (20, 40, 60), (0, y, self.list_w, 32))
                pygame.draw.line(surf, theme.ACCENT, (0, y), (0, y+32), 4)
            color = theme.ACCENT if sel else theme.FG_DIM
            name = pygame.font.Font(None, 22).render(cam.id, True, color)
            surf.blit(name, (15, y + 6))

        # Viewport
        view = pygame.Rect(self.list_w + 20, head.bottom + 20, self.view_w, self.view_h)
        pygame.draw.rect(surf, (0, 0, 0), view)
        pygame.draw.rect(surf, theme.ACCENT_DIM, view, 2)

        current_ip = "UNKNOWN"
        if self._frame_buffer:
            frame, current_ip = self._frame_buffer[0]
            surf.blit(frame, view.topleft)
            for y in range(view.y, view.bottom, 4):
                pygame.draw.line(surf, (0, 0, 0, 80), (view.x, y), (view.right, y))
        
        surf.blit(random.choice(self._noise_cache), view.topleft)

        # OSD
        f_small = pygame.font.Font(None, 22)
        cam = self.cameras[self.selected]
        surf.blit(f_small.render(f"TARGET: {cam.location} | {current_ip}", True, theme.ACCENT), (view.x + 10, view.y + 10))
        surf.blit(f_small.render(f"SIGNAL: {self.fps:.1f} FPS | ZOOM: {self.zoom}X", True, theme.FG), (view.x + 10, view.bottom - 25))
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ts_surf = f_small.render(ts, True, theme.FG)
        surf.blit(ts_surf, (view.right - ts_surf.get_width() - 10, view.y + 10))

        if self.is_loading:
            msg = f_small.render("ESTABLISHING LINK...", True, theme.ACCENT)
            surf.blit(msg, (view.centerx - msg.get_width()//2, view.centery))
        elif self.error_msg and not self._frame_buffer:
            err = f_small.render(f"ERR: {self.error_msg[:30]}", True, theme.ERR)
            surf.blit(err, (view.centerx - err.get_width()//2, view.centery))
        
        hint = f_small.render("L/R: Cam  UP: Zoom  DOWN: Cycle  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (view.x, view.bottom + 10))
