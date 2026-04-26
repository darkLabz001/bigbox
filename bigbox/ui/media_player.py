"""Media Player — File browser and playback UI."""
from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App


class MediaPlayerView:
    def __init__(self, media_dir: str = "media") -> None:
        self.media_dir = media_dir
        self.dismissed = False
        self.playing_file: str | None = None
        self.proc: subprocess.Popen | None = None
        
        try:
            if not os.path.exists(self.media_dir):
                os.makedirs(self.media_dir)
        except Exception as e:
            print(f"[media] Failed to create dir: {e}")

        self.list = self._refresh_list()
        
        # Cache fonts to avoid re-loading every frame
        try:
            self.title_font = pygame.font.Font(None, theme.FS_TITLE)
            self.body_font = pygame.font.Font(None, theme.FS_BODY)
            self.hint_font = pygame.font.Font(None, theme.FS_SMALL)
            self.play_font = pygame.font.Font(None, 64) # Reduced size for safety
        except Exception as e:
            print(f"[media] Font init error: {e}")
            # Fallback to default small font if anything fails
            self.title_font = self.body_font = self.hint_font = self.play_font = pygame.font.SysFont("monospace", 20)

    def _refresh_list(self) -> ScrollList:
        files = []
        try:
            if os.path.exists(self.media_dir):
                files = sorted([f for f in os.listdir(self.media_dir) if os.path.isfile(os.path.join(self.media_dir, f))])
        except Exception as e:
            print(f"[media] List error: {e}")
        
        actions = []
        for f in files:
            def make_handler(filename: str):
                return lambda ctx: self._play(filename)
            actions.append(Action(f, make_handler(f)))
        
        if not actions:
            actions.append(Action("[ No media found ]", None, "Upload via Web UI"))
            
        return ScrollList(actions)

    def _play(self, filename: str) -> None:
        self.playing_file = filename
        full_path = os.path.abspath(os.path.join(self.media_dir, filename))
        
        print(f"[media] Playing: {full_path}")
        
        try:
            # Check if vlc exists
            vlc_path = subprocess.check_output(["which", "vlc"], text=True).strip()
            
            # Create a clean environment for VLC
            env = os.environ.copy()
            env["DISPLAY"] = ":0"
            
            # Launch VLC. 
            # --allow-run-as-root: REQUIRED when running as root service
            # --fullscreen: takes over the display
            # --no-video-title-show: cleaner look
            # --play-and-exit: returns when done
            self.proc = subprocess.Popen(
                [vlc_path, "--allow-run-as-root", "--fullscreen", "--no-video-title-show", "--play-and-exit", full_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env
            )
        except Exception as e:
            print(f"[media] VLC launch error: {e}")
            self.proc = None

    def _stop(self) -> None:
        if self.proc:
            print("[media] Stopping playback")
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self.playing_file = None

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        try:
            if not ev.pressed:
                return

            if ev.button is Button.B:
                if self.playing_file:
                    self._stop()
                else:
                    self.dismissed = True
                return

            if self.playing_file:
                # Stop on any of these buttons while playing
                if ev.button in (Button.A, Button.START, Button.SELECT):
                    self._stop()
                return

            action = self.list.handle(ev)
            if action and action.handler:
                action.handler(ctx)
        except Exception as e:
            print(f"[media] Handle error: {e}")

    def render(self, surf: pygame.Surface) -> None:
        # Check if process finished
        if self.playing_file and self.proc:
            if self.proc.poll() is not None:
                print("[media] Playback finished naturally")
                self.playing_file = None
                self.proc = None

        try:
            surf.fill(theme.BG)
            
            # Header
            head_h = 60
            pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
            pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1), (theme.SCREEN_W, head_h - 1), 2)
            
            title_text = "MEDIA PLAYER" if not self.playing_file else f"PLAYING: {self.playing_file}"
            title = self.title_font.render(title_text, True, theme.ACCENT)
            surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

            if self.playing_file:
                center_x, center_y = theme.SCREEN_W // 2, theme.SCREEN_H // 2
                
                box_w, box_h = 600, 340
                box = pygame.Rect(center_x - box_w // 2, center_y - box_h // 2 + 20, box_w, box_h)
                pygame.draw.rect(surf, (0, 0, 0), box)
                pygame.draw.rect(surf, theme.ACCENT_DIM, box, 2)
                
                icon = self.play_font.render("▶", True, theme.ACCENT)
                surf.blit(icon, (center_x - icon.get_width() // 2, center_y - icon.get_height() // 2 + 20))
                
                msg = "Playing in background..." if self.proc else "Error: VLC failed to start."
                color = theme.FG_DIM if self.proc else theme.ERR
                hint = self.hint_font.render(f"{msg} Press A or B to return.", True, color)
                surf.blit(hint, (center_x - hint.get_width() // 2, box.bottom + 20))
                
            else:
                list_rect = pygame.Rect(
                    theme.PADDING,
                    head_h + theme.PADDING,
                    theme.SCREEN_W - 2 * theme.PADDING,
                    theme.SCREEN_H - head_h - 2 * theme.PADDING - 40
                )
                self.list.render(surf, list_rect, self.body_font)
                
                hint = self.hint_font.render("UP/DOWN: Navigate  A: Play  B: Back", True, theme.FG_DIM)
                surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
        except Exception as e:
            print(f"[media] Render error: {e}")
            # If rendering fails, we don't want to just show a black screen forever,
            # but we also don't want to crash the whole app.
