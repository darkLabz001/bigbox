"""Internet TV — Watch free internet TV channels via HLS transcoding."""
from __future__ import annotations

import io
import os
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
    # --- News & Business ---
    TVChannel("ABC News", "https://abc-news-dmd-streams-1.akamaized.net/out/v1/701126012d044971b3fa89406a440133/index.m3u8", "News"),
    TVChannel("CBS News 24/7", "https://jmp2.uk/plu-6350fdd266e9ea0007bedec5.m3u8", "News"),
    TVChannel("NBC News NOW", "https://d1si3n1st4nkgb.cloudfront.net/10502/88896001/hls/master.m3u8", "News"),
    TVChannel("Bloomberg Originals", "https://86fdc85a.wurl.com/master/f36d25e7e52f1ba8d7e56eb859c636563214f541/TEctZ2JfQmxvb21iZXJnT3JpZ2luYWxzX0hMUw/playlist.m3u8", "Business"),
    TVChannel("Al Jazeera English", "https://live-hls-apps-aje-fa.getaj.net/AJE/index.m3u8", "News"),
    TVChannel("France 24 English", "https://live.france24.com/hls/live/2037218-b/F24_EN_HI_HLS/master_5000.m3u8", "News"),
    TVChannel("DW English", "https://dwamdstream102.akamaized.net/hls/live/2015430/dwstream102/master.m3u8", "News"),
    TVChannel("Sky News Extra", "https://skynewsau-live.akamaized.net/hls/live/2002689/skynewsau-extra1/master.m3u8", "News"),
    TVChannel("Reuters", "https://amg00453-reuters-amg00453c1-rakuten-uk-2110.playouts.now.amagi.tv/playlist/amg00453-reuters-reuters-rakutenuk/playlist.m3u8", "News"),
    TVChannel("CBC News", "https://d2ny9lo79ujali.cloudfront.net/CBC_News_International.m3u8", "News"),
    TVChannel("CNA Originals", "https://d2e1asnsl7br7b.cloudfront.net/7782e205e72f43aeb4a48ec97f66ebbe/index.m3u8", "News"),

    # --- Entertainment & Movies ---
    TVChannel("Classic Movies", "https://jmp2.uk/plu-5f4d878d3d19b30007d2e782.m3u8", "Movies"),
    TVChannel("70s Cinema", "https://jmp2.uk/plu-5f4d878d3d19b30007d2e782.m3u8", "Movies"),
    TVChannel("80s Rewind", "https://jmp2.uk/plu-5ca525b650be2571e3943c63.m3u8", "Movies"),
    TVChannel("90s Throwback", "https://jmp2.uk/plu-5f4d86f519358a00072b978e.m3u8", "Movies"),
    TVChannel("50 Cent Action", "https://jmp2.uk/plu-68487fb3f212bedacf5a53e3.m3u8", "Action"),
    TVChannel("Gravitas Movies", "https://jmp2.uk/plu-5ca5258950be2571e3943c62.m3u8", "Movies"),
    TVChannel("Star Trek", "https://jmp2.uk/plu-5d2c56a8aeb3e2738ae27932.m3u8", "Sci-Fi"),
    TVChannel("Doctor Who Classic", "https://jmp2.uk/plu-5e1f7da4bc7d740009831259.m3u8", "Sci-Fi"),
    TVChannel("CSI: NY", "https://jmp2.uk/plu-62e925bc68d18a00077bb990.m3u8", "Series"),
    TVChannel("Top Gear", "https://jmp2.uk/plu-5ca52a1b50be2571e3943c74.m3u8", "Entertainment"),

    # --- Sports & Science ---
    TVChannel("Red Bull TV", "https://3ea22335.wurl.com/master/f36d25e7e52f1ba8d7e56eb859c636563214f541/UmFrdXRlblRWLWdiX1JlZEJ1bGxUVl9ITFM/playlist.m3u8", "Sports"),
    TVChannel("NASA TV", "https://ntvpublic.akamaized.net/hls/live/2026507/NASA-NTV1-Public/master.m3u8", "Science"),
    TVChannel("Gusto TV", "https://jmp2.uk/plu-5da667e41154560009581831.m3u8", "Food"),
    TVChannel("ACCDN", "https://raycom-accdn-firetv.amagi.tv/playlist.m3u8", "Sports"),
    TVChannel("30A Outdoor", "https://30a-tv.com/darcizzle.m3u8", "Outdoor"),
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
        
        self.playing_proc: subprocess.Popen | None = None
        
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
        if self.playing_proc:
            return
            
        if not shutil.which("mpv"):
            self.error_msg = "mpv not installed"
            return

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
            if shutil.which("amixer"):
                subprocess.run(["amixer", "-c", "1", "sset", "PCM", "100%", "unmute"], capture_output=True)
            
            self.playing_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env
            )
        except Exception as e:
            self.error_msg = f"launch failed: {e}"
            self._start_stream_thread()

    def _stop_fullscreen(self):
        if self.playing_proc:
            try:
                self.playing_proc.terminate()
                self.playing_proc.wait(timeout=1.0)
            except:
                try:
                    self.playing_proc.kill()
                except:
                    pass
            self.playing_proc = None
        self._start_stream_thread()

    def handle(self, ev: ButtonEvent, ctx: any) -> None:
        if not ev.pressed:
            return
            
        if self.playing_proc:
            if ev.button in (Button.A, Button.B, Button.START):
                self._stop_fullscreen()
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
        # Check if playback ended externally (e.g. user pressed 'q' in mpv)
        if self.playing_proc and self.playing_proc.poll() is not None:
            self.playing_proc = None
            self._start_stream_thread()

        surf.fill(theme.BG)
        
        # Header
        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1), (theme.SCREEN_W, head_h - 1), 2)
        
        title = pygame.font.Font(None, 36).render("INTERNET TV", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))
        
        # Channel List (Left)
        list_y = head_h + theme.PADDING
        # Calculate scroll offset to keep selected visible
        row_h = 40
        max_rows = (theme.SCREEN_H - list_y - 40) // row_h
        scroll_idx = max(0, self.selected - max_rows // 2)
        if scroll_idx + max_rows > len(self.channels):
            scroll_idx = max(0, len(self.channels) - max_rows)

        for i in range(scroll_idx, min(scroll_idx + max_rows, len(self.channels))):
            chan = self.channels[i]
            sel = i == self.selected
            y = list_y + (i - scroll_idx) * row_h
                
            rect = pygame.Rect(theme.PADDING, y, self.list_w - 20, 36)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=4)
                color = theme.ACCENT
            else:
                color = theme.FG_DIM
                
            name_text = chan.name
            if len(name_text) > 20: name_text = name_text[:18] + ".."
            name = pygame.font.Font(None, 24).render(name_text, True, color)
            surf.blit(name, (rect.x + 10, rect.y + (rect.height - name.get_height()) // 2))
            
            cat_text = chan.category.split(';')[0]
            cat = pygame.font.Font(None, 18).render(cat_text, True, theme.FG_DIM if not sel else theme.ACCENT_DIM)
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
        if self.playing_proc:
            tag_text = f"PLAYING :: {chan.name.upper()}"
        else:
            tag_text = f"LIVE :: {chan.name.upper()}"
        tag = f_small.render(tag_text, True, theme.ACCENT)
        surf.blit(tag, (view_rect.x + 15, view_rect.y + 15))
        
        # Bottom-right OSD
        ts = datetime.now().strftime("%H:%M:%S")
        ts_surf = f_small.render(ts, True, theme.FG)
        surf.blit(ts_surf, (view_rect.right - ts_surf.get_width() - 15, view_rect.bottom - 25))
        
        # Status indicators
        if self.playing_proc:
            msg = f_small.render("FULL SCREEN ACTIVE - PRESS A/B TO STOP", True, theme.ACCENT)
            surf.blit(msg, (view_rect.centerx - msg.get_width() // 2, view_rect.centery))
        elif self.is_loading:
            msg = f_small.render("BUFFERING...", True, theme.ACCENT)
            surf.blit(msg, (view_rect.centerx - msg.get_width() // 2, view_rect.centery))
        elif self.error_msg and not self._frame_buffer:
            err = f_small.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err, (view_rect.centerx - err.get_width() // 2, view_rect.centery))

        # Controls Hint
        if self.playing_proc:
            hint_text = "A/B: Stop Playback"
        else:
            hint_text = "UP/DOWN: Channels  A: FULL SCREEN  B: Back"
        hint = f_small.render(hint_text, True, theme.FG_DIM)
        surf.blit(hint, (view_rect.x, theme.SCREEN_H - 30))
