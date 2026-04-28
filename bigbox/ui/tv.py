"""Internet TV — Watch free internet TV channels via HLS transcoding."""
from __future__ import annotations

import io
import random
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action


@dataclass
class TVChannel:
    name: str
    url: str
    category: str = "General"


CHANNELS = [
    TVChannel("ABC News", "https://content.uplynk.com/channel/3324f2467c414329b3b4cc61b055776e.m3u8", "News"),
    TVChannel("NHK World", "https://nhkwlive-ojp.akamaized.net/hls/live/2003459/nhkwlive-ojp-en/master.m3u8", "News"),
    TVChannel("Al Jazeera", "https://live-hls-web-aje.getaj.net/AJE/index.m3u8", "News"),
    TVChannel("DW English", "https://dwamdstream102.akamaized.net/hls/live/2015430/dwstream102/master.m3u8", "News"),
    TVChannel("France 24", "https://static.france24.com/live/F24_EN_LO_HLS/live_web.m3u8", "News"),
    TVChannel("Sky News", "https://skynewsau-live.akamaized.net/hls/live/2002689/skynewsau-en/master.m3u8", "News"),
    TVChannel("Bloomberg", "https://live-bloomberg-us-east.ateme.com/index.m3u8", "Business"),
    TVChannel("Red Bull TV", "https://rbmn-live.akamaized.net/hls/live/590964/flrb/master.m3u8", "Sports"),
    TVChannel("Fashion TV", "https://fash1043.cloudycdn.be/slive/_definst_/ftv_ftv_mid_600/playlist.m3u8", "Lifestyle"),
    TVChannel("NASA TV", "https://ntvpublic.akamaized.net/hls/live/2026507/NASA-NTV1-Public/master.m3u8", "Science"),
]


class InternetTVView:
    """Full-screen TV player with channel list and live preview."""

    def __init__(self) -> None:
        self.channels = CHANNELS
        self.selected = 0
        self.dismissed = False
        
        # UI dimensions
        self.list_w = 240
        self.view_w = 520
        self.view_h = 360
        
        # State
        self._frame_buffer = deque(maxlen=1)
        self.is_loading = True
        self.error_msg: str | None = None
        self.fps = 0.0
        
        # Noise for "static" effect
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
                surf.set_at(
                    (random.randint(0, self.view_w - 1), random.randint(0, self.view_h - 1)),
                    (random.randint(10, 60),) * 3
                )
            surf.set_alpha(80)
            self._noise_cache.append(surf)

    def _start_stream_thread(self):
        if self._fetch_thread and self._fetch_thread.is_alive():
            self._stop_thread = True
            self._fetch_thread.join(timeout=1.0)
        
        self._stop_thread = False
        self._fetch_thread = threading.Thread(target=self._fetch_loop, daemon=True)
        self._fetch_thread.start()

    def _fetch_loop(self) -> None:
        while not self._stop_thread:
            chan = self.channels[self.selected]
            current_idx = self.selected
            self.is_loading = True
            self.error_msg = None

            if not shutil.which("ffmpeg"):
                self.error_msg = "ffmpeg not found"
                time.sleep(2)
                continue

            # ffmpeg command to transcode HLS to MJPEG on stdout
            cmd = [
                "ffmpeg",
                "-loglevel", "error",
                "-hide_banner",
                "-fflags", "nobuffer",
                "-flags", "low_delay",
                "-probesize", "32",
                "-analyzeduration", "0",
                "-user_agent", "Mozilla/5.0",
                "-i", chan.url,
                "-vf", f"scale={self.view_w}:{self.view_h}:force_original_aspect_ratio=decrease,"
                       f"pad={self.view_w}:{self.view_h}:(ow-iw)/2:(oh-ih)/2",
                "-r", "15",
                "-q:v", "5",
                "-an",
                "-f", "mjpeg",
                "pipe:1",
            ]
            
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except Exception as e:
                self.error_msg = str(e)
                time.sleep(2)
                continue

            self.is_loading = False
            buf = bytearray()
            last_fps_check = time.time()
            frames_this_sec = 0
            
            try:
                while not self._stop_thread and self.selected == current_idx:
                    chunk = proc.stdout.read(32768) if proc.stdout else b""
                    if not chunk:
                        break
                    
                    buf.extend(chunk)
                    while True:
                        a = buf.find(b"\xff\xd8")
                        b = buf.find(b"\xff\xd9", a + 2)
                        if a == -1 or b == -1:
                            break
                        
                        jpg = bytes(buf[a:b + 2])
                        del buf[:b + 2]
                        
                        try:
                            raw_surf = pygame.image.load(io.BytesIO(jpg))
                            self._frame_buffer.append(raw_surf)
                            frames_this_sec += 1
                            now = time.time()
                            if now - last_fps_check > 1.0:
                                self.fps = frames_this_sec
                                frames_this_sec = 0
                                last_fps_check = now
                        except Exception:
                            pass
                    
                    if len(buf) > 1024 * 1024:
                        buf = bytearray()
            finally:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            
            if self._stop_thread:
                break
            
            # If we exited the inner loop but didn't change channels, there was an error
            if self.selected == current_idx:
                self.error_msg = "Stream disconnected"
                time.sleep(2)

    def _play_fullscreen(self):
        """Launches mpv for true full-screen playback with audio."""
        chan = self.channels[self.selected]
        # We need to stop our preview thread so it doesn't fight for bandwidth/CPU
        self._stop_thread = True
        if self._fetch_thread:
            self._fetch_thread.join(timeout=1.0)
        
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        
        # Match media_player.py settings
        cmd = [
            "mpv",
            "--vo=x11",
            "--fs",
            "--cursor-autohide=always",
            "--ao=alsa,pulse,null",
            "--volume=100",
            "--no-osc",
            chan.url,
        ]
        
        try:
            # Re-enforce volume
            subprocess.run(["amixer", "sset", "PCM", "100%"], capture_output=True)
            # This is blocking in terms of the subprocess, but we want it to run
            # until the user exits mpv.
            subprocess.run(cmd, env=env)
        except Exception as e:
            print(f"[tv] Fullscreen error: {e}")
        
        # Restart preview
        self._start_stream_thread()

    def handle(self, ev: ButtonEvent, ctx: any) -> None:
        if not ev.pressed:
            return
            
        if ev.button is Button.B:
            self._stop_thread = True
            self.dismissed = True
        elif ev.button is Button.UP:
            self.selected = (self.selected - 1) % len(self.channels)
            self._frame_buffer.clear()
            self.fps = 0.0
        elif ev.button is Button.DOWN:
            self.selected = (self.selected + 1) % len(self.channels)
            self._frame_buffer.clear()
            self.fps = 0.0
        elif ev.button is Button.A:
            # Start full-screen playback
            self._play_fullscreen()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1), (theme.SCREEN_W, head_h - 1), 2)
        
        title = pygame.font.Font(None, 36).render("INTERNET TV", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))
        
        # Channel List (Left)
        list_y = head_h + theme.PADDING
        for i, chan in enumerate(self.channels):
            sel = i == self.selected
            y = list_y + i * 40
            if y > theme.SCREEN_H - 40:
                break
                
            rect = pygame.Rect(theme.PADDING, y, self.list_w - 20, 36)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=4)
                color = theme.ACCENT
            else:
                color = theme.FG_DIM
                
            name = pygame.font.Font(None, 24).render(chan.name, True, color)
            surf.blit(name, (rect.x + 10, rect.y + (rect.height - name.get_height()) // 2))
            
            cat = pygame.font.Font(None, 18).render(chan.category, True, theme.FG_DIM if not sel else theme.ACCENT_DIM)
            surf.blit(cat, (rect.right - cat.get_width() - 5, rect.y + 20))

        # Viewport (Right)
        view_x = self.list_w + theme.PADDING
        view_y = head_h + theme.PADDING
        view_rect = pygame.Rect(view_x, view_y, self.view_w, self.view_h)
        
        pygame.draw.rect(surf, (0, 0, 0), view_rect)
        pygame.draw.rect(surf, theme.ACCENT_DIM, view_rect, 2)
        
        if self._frame_buffer:
            frame = self._frame_buffer[0]
            surf.blit(frame, view_rect.topleft)
            # Scanlines
            for y in range(view_rect.y, view_rect.bottom, 4):
                pygame.draw.line(surf, (0, 0, 0, 100), (view_rect.x, y), (view_rect.right, y))
        
        # Static overlay
        surf.blit(random.choice(self._noise_cache), view_rect.topleft)
        
        # OSD
        f_small = pygame.font.Font(None, 22)
        chan = self.channels[self.selected]
        
        # Top-left OSD
        tag = f_small.render(f"LIVE :: {chan.name.upper()}", True, theme.ACCENT)
        surf.blit(tag, (view_rect.x + 15, view_rect.y + 15))
        
        # Bottom-right OSD
        ts = datetime.now().strftime("%H:%M:%S")
        ts_surf = f_small.render(ts, True, theme.FG)
        surf.blit(ts_surf, (view_rect.right - ts_surf.get_width() - 15, view_rect.bottom - 25))
        
        # Status indicators
        if self.is_loading:
            msg = f_small.render("BUFFERING...", True, theme.ACCENT)
            surf.blit(msg, (view_rect.centerx - msg.get_width() // 2, view_rect.centery))
        elif self.error_msg and not self._frame_buffer:
            err = f_small.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err, (view_rect.centerx - err.get_width() // 2, view_rect.centery))

        # Controls Hint
        hint = f_small.render("UP/DOWN: Channels  A: FULL SCREEN  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (view_rect.x, theme.SCREEN_H - 30))
