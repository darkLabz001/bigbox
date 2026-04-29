"""Traffic Cam — Public traffic camera browser."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

@dataclass
class TrafficCamera:
    name: str
    location: str
    url: str
    category: str = "Traffic"

# Curated list of high-availability public traffic/weather streams
CAMERAS = [
    TrafficCamera("NYC - Times Square", "New York, NY", "https://video-auth1.iol.it/edge/HLS/TIMES_SQUARE_NY/index.m3u8"),
    TrafficCamera("London - Abbey Road", "London, UK", "https://shstream.net/hls/abbeyroad.m3u8"),
    TrafficCamera("Tokyo - Shibuya", "Tokyo, Japan", "https://nhkwlive-ojp.akamaized.net/hls/live/2003459/nhkwlive-ojp-en/master.m3u8"), # NHK Tokyo News
    TrafficCamera("Seattle - I-5 at Denny", "Seattle, WA", "https://61e0c5d388c2e.streamlock.net:443/live/1_N_Denny_EW.stream/playlist.m3u8"),
    TrafficCamera("Vegas - The Strip", "Las Vegas, NV", "https://rtsp.me/embed/n4Kz3h3a/"), # Example web link
    TrafficCamera("Chicago - Wacker Dr", "Chicago, IL", "https://61e0c5d388c2e.streamlock.net:443/live/7_Wacker_Dr.stream/playlist.m3u8"),
    TrafficCamera("Atlanta - I-75/85", "Atlanta, GA", "https://61e0c5d388c2e.streamlock.net:443/live/Atlanta_Traffic.stream/playlist.m3u8"),
    TrafficCamera("Houston - Westheimer", "Houston, TX", "https://61e0c5d388c2e.streamlock.net:443/live/Houston_West.stream/playlist.m3u8"),
]

class TrafficCamView:
    def __init__(self) -> None:
        self.dismissed = False
        self.selected_idx = 0
        self.playing_proc: Optional[subprocess.Popen] = None
        self.status_msg = "SELECT FEED"
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 20)

    def _play_cam(self):
        if self.playing_proc: return
        cam = CAMERAS[self.selected_idx]
        self.status_msg = f"OPENING: {cam.name}"
        
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        
        cmd = [
            "mpv",
            "--vo=x11",
            "--fs",
            "--no-osc",
            "--no-audio",
            cam.url
        ]
        
        try:
            self.playing_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env
            )
        except Exception as e:
            self.status_msg = f"ERROR: {e}"

    def _stop_cam(self):
        if self.playing_proc:
            try:
                self.playing_proc.terminate()
                self.playing_proc.wait(timeout=1)
            except:
                try: self.playing_proc.kill()
                except: pass
            self.playing_proc = None
            self.status_msg = "FEED TERMINATED"

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        
        if self.playing_proc:
            if ev.button in (Button.A, Button.B, Button.START):
                self._stop_cam()
            return

        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.UP:
            self.selected_idx = (self.selected_idx - 1) % len(CAMERAS)
        elif ev.button is Button.DOWN:
            self.selected_idx = (self.selected_idx + 1) % len(CAMERAS)
        elif ev.button is Button.A:
            self._play_cam()

    def render(self, surf: pygame.Surface) -> None:
        if self.playing_proc and self.playing_proc.poll() is not None:
            self.playing_proc = None
            self.status_msg = "FEED ENDED"

        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("RECON :: TRAFFIC_CAM_BROWSER", True, theme.ACCENT), (theme.PADDING, 8))
        
        # Camera List
        list_x = 20
        list_y = head_h + 20
        list_w = 400
        for i, cam in enumerate(CAMERAS):
            sel = i == self.selected_idx
            rect = pygame.Rect(list_x, list_y + i * 45, list_w, 40)
            bg = (30, 30, 50) if sel else (15, 15, 25)
            pygame.draw.rect(surf, bg, rect, border_radius=4)
            if sel: pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=4)
            surf.blit(self.f_main.render(cam.name, True, theme.ACCENT if sel else theme.FG), (rect.x + 15, rect.y + 10))

        # Preview / Detail Panel
        det_x = list_x + list_w + 40
        det_y = list_y
        cam = CAMERAS[self.selected_idx]
        surf.blit(self.f_main.render("TARGET_INFO", True, theme.ACCENT), (det_x, det_y))
        pygame.draw.line(surf, theme.DIVIDER, (det_x, det_y + 25), (theme.SCREEN_W - 20, det_y + 25))
        
        info = [
            ("NAME:", cam.name),
            ("LOCATION:", cam.location),
            ("CATEGORY:", cam.category),
            ("STATUS:", "STREAM_AVAILABLE"),
        ]
        for i, (lbl, val) in enumerate(info):
            surf.blit(self.f_small.render(lbl, True, theme.FG_DIM), (det_x, det_y + 40 + i * 30))
            surf.blit(self.f_small.render(val, True, theme.FG), (det_x + 100, det_y + 40 + i * 30))

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        surf.blit(self.f_small.render(f"STATUS: {self.status_msg}", True, theme.ACCENT), (10, theme.SCREEN_H - 26))
        hint = "A: VIEW FULLSCREEN  B: BACK" if not self.playing_proc else "A/B: STOP VIEWING"
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))
