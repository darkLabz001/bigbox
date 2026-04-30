"""YouTube — High-fidelity tactical YouTube browser using yt-dlp and mpv."""
from __future__ import annotations

import os
import re
import shutil
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
class YouTubeVideo:
    id: str
    title: str
    duration: str
    author: str = "Unknown"

# Curated tactical/hacker channels for the landing page
CURATED = [
    ("DEF CON", "UC6m96shsNTSf-mYis8S7HAA"),
    ("Hak5", "UC3s0BtrBJpw9ZteJOCkId5w"),
    ("Loi Liang Yang", "UC7-dfO0W292fQ5O6m8Dmg9A"),
    ("NetworkChuck", "UC9x0AN7BpJpS67MByKjK69A"),
    ("The Cyber Mentor", "UC0ArkuBa9mfCf79U7JkeMbg"),
    ("IppSec", "UCa6eh7gCkp78psBAWv2QPrw"),
]

PHASE_LANDING = "landing"
PHASE_SEARCH = "search"
PHASE_RESULTS = "results"
PHASE_PLAYING = "playing"

class YouTubeView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.status_msg = "UPLINK_READY"
        
        self.results: List[YouTubeVideo] = []
        self.selected_idx = 0
        self.scroll = 0
        
        self.is_loading = False
        self.playing_proc: Optional[subprocess.Popen] = None
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_med = pygame.font.Font(None, 22)
        self.f_small = pygame.font.Font(None, 20)
        self.f_tiny = pygame.font.Font(None, 16)

    def _perform_search(self, query: str | None):
        if not query:
            return
        self.phase = PHASE_RESULTS
        self.is_loading = True
        self.status_msg = f"SEARCHING: {query.upper()}..."
        self.results = []
        self.selected_idx = 0
        self.scroll = 0
        
        def _worker():
            try:
                # Use yt-dlp to get top 15 results
                cmd = ["yt-dlp", f"ytsearch15:{query}", "--get-title", "--get-id", "--get-duration", "--no-playlist"]
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                lines = out.strip().split('\n')
                
                new_results = []
                # yt-dlp returns: Title, ID, Duration in sequence
                for i in range(0, len(lines)-2, 3):
                    new_results.append(YouTubeVideo(
                        title=lines[i],
                        id=lines[i+1],
                        duration=lines[i+2]
                    ))
                
                self.results = new_results
                self.status_msg = f"FOUND {len(self.results)} RESULTS"
            except Exception as e:
                self.status_msg = f"SEARCH_ERROR: {e}"
            self.is_loading = False
            
        threading.Thread(target=_worker, daemon=True).start()

    def _play_video(self, video: YouTubeVideo):
        self.status_msg = f"BUFFERING: {video.title[:20]}..."
        
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        
        # Optimized mpv flags for Pi
        cmd = [
            "mpv",
            "--vo=x11",
            "--fs",
            "--cursor-autohide=always",
            "--ao=alsa,pulse,null",
            "--volume=100",
            "--no-osc",
            "--force-window",
            f"https://www.youtube.com/watch?v={video.id}"
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
            self.status_msg = "PLAYING_STREAM"
        except Exception as e:
            self.status_msg = f"PLAY_ERROR: {e}"

    def _stop_video(self):
        if self.playing_proc:
            try:
                self.playing_proc.terminate()
                self.playing_proc.wait(timeout=1)
            except:
                try: self.playing_proc.kill()
                except: pass
            self.playing_proc = None
            self.status_msg = "STREAM_TERMINATED"

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        
        if self.playing_proc:
            if ev.button in (Button.A, Button.B, Button.START):
                self._stop_video()
            return

        if ev.button is Button.B:
            if self.phase == PHASE_RESULTS:
                self.phase = PHASE_LANDING
                self.status_msg = "UPLINK_READY"
            else:
                self.dismissed = True
            return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                ctx.get_input("SEARCH YOUTUBE", self._perform_search)
            elif ev.button is Button.X:
                # Refresh curated? 
                pass

        elif self.phase == PHASE_RESULTS:
            if not self.results: return
            if ev.button is Button.UP:
                self.selected_idx = max(0, self.selected_idx - 1)
                self._adjust_scroll()
            elif ev.button is Button.DOWN:
                self.selected_idx = min(len(self.results) - 1, self.selected_idx + 1)
                self._adjust_scroll()
            elif ev.button is Button.A:
                self._play_video(self.results[self.selected_idx])

    def _adjust_scroll(self):
        visible = 9
        if self.selected_idx < self.scroll:
            self.scroll = self.selected_idx
        elif self.selected_idx >= self.scroll + visible:
            self.scroll = self.selected_idx - visible + 1

    def render(self, surf: pygame.Surface) -> None:
        if self.playing_proc and self.playing_proc.poll() is not None:
            self.playing_proc = None
            self.status_msg = "STREAM_FINISHED"

        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("MEDIA :: YOUTUBE_UPLINK", True, theme.ACCENT), (theme.PADDING, 8))
        
        if self.phase == PHASE_LANDING:
            self._render_landing(surf, head_h)
        elif self.phase == PHASE_RESULTS:
            self._render_results(surf, head_h)

        # Loading Overlay
        if self.is_loading:
            overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 150))
            surf.blit(overlay, (0, 0))
            msg = self.f_med.render("ACCESSING NEURAL NET...", True, theme.ACCENT)
            surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, theme.SCREEN_H//2))

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        surf.blit(self.f_small.render(f"STATUS: {self.status_msg}", True, theme.ACCENT), (10, theme.SCREEN_H - 26))
        
        hint = "A: SEARCH  B: BACK" if self.phase == PHASE_LANDING else "A: PLAY  B: BACK"
        if self.playing_proc: hint = "A/B: STOP_PLAYBACK"
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        y = head_h + 60
        center_x = theme.SCREEN_W // 2
        
        # Big Play Icon
        pygame.draw.circle(surf, theme.ACCENT_DIM, (center_x, y + 40), 50, 2)
        pygame.draw.polygon(surf, theme.ACCENT, [
            (center_x - 15, y + 20),
            (center_x - 15, y + 60),
            (center_x + 25, y + 40)
        ])
        
        surf.blit(self.f_title.render("PRESS A TO SEARCH THE ARCHIVE", True, theme.FG), (center_x - 180, y + 120))
        
        # Curated list hint
        surf.blit(self.f_small.render("POPULAR CHANNELS:", True, theme.FG_DIM), (50, y + 200))
        for i, (name, _) in enumerate(CURATED[:4]):
            surf.blit(self.f_tiny.render(f"• {name}", True, theme.ACCENT_DIM), (60, y + 230 + i * 20))

    def _render_results(self, surf: pygame.Surface, head_h: int):
        list_rect = pygame.Rect(10, head_h + 10, theme.SCREEN_W - 20, theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (5, 5, 10), list_rect, border_radius=4)
        
        if not self.results and not self.is_loading:
            surf.blit(self.f_main.render("NO_RESULTS_FOUND", True, theme.ERR), (list_rect.centerx - 80, list_rect.centery))
            return

        visible = self.results[self.scroll : self.scroll + 9]
        for i, vid in enumerate(visible):
            idx = self.scroll + i
            sel = idx == self.selected_idx
            y = list_rect.y + 10 + i * 40
            
            if sel:
                pygame.draw.rect(surf, (30, 30, 50), (15, y-2, list_rect.width-10, 36), border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, (15, y-2, list_rect.width-10, 36), 1, border_radius=4)
            
            color = theme.ACCENT if sel else theme.FG
            title_text = vid.title
            if len(title_text) > 75: title_text = title_text[:72] + "..."
            surf.blit(self.f_main.render(title_text, True, color), (30, y + 5))
            
            dur = self.f_tiny.render(vid.duration, True, theme.FG_DIM)
            surf.blit(dur, (theme.SCREEN_W - dur.get_width() - 30, y + 10))
