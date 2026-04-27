"""Media Player — File browser and playback UI."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App


# mpv's combined stdout+stderr lands here. We tail this when a launch
# fails so the on-screen error tells the user *why* instead of the old
# "Error: VLC failed to start." stub.
MPV_LOG = "/tmp/bigbox-mpv.log"


class MediaPlayerView:
    def __init__(self, media_dir: str = "media") -> None:
        self.media_dir = media_dir
        self.dismissed = False
        self.playing_file: str | None = None
        self.proc: subprocess.Popen | None = None
        self.error_msg: str | None = None
        self._launch_time: float = 0.0
        
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
        self.error_msg = None
        self.proc = None
        full_path = os.path.abspath(os.path.join(self.media_dir, filename))

        print(f"[media] Playing: {full_path}")

        if not shutil.which("mpv"):
            self.error_msg = "mpv not installed (apt install mpv)"
            print(f"[media] {self.error_msg}")
            return
        if not os.path.exists(full_path):
            self.error_msg = f"file not found: {full_path}"
            print(f"[media] {self.error_msg}")
            return

        # Bigbox runs as root under xinit, so mpv inherits the X session
        # and ALSA access without any sudo / Xauthority gymnastics.
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XAUTHORITY", "/root/.Xauthority")

        # Capture stderr+stdout to a log file so a failed launch leaves
        # diagnostics behind.
        try:
            log_fd: int | object = open(MPV_LOG, "w")
        except Exception:
            log_fd = subprocess.DEVNULL

        cmd = [
            "mpv",
            "--fs",                          # fullscreen
            "--no-border",                   # no WM decorations under bare xinit
            "--ontop",                       # raise above pygame surface
            "--cursor-autohide=always",
            "--ao=alsa",                     # ALSA, no PulseAudio session req'd
            "--hwdec=auto-safe",             # v4l2m2m on Pi 4 where available
            "--no-osc",                      # bigbox owns the controls
            "--no-input-default-bindings",   # don't react to keyboard
            "--really-quiet",
            full_path,
        ]
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,    # never read from controlling tty
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                env=env,
            )
            self._launch_time = time.time()
        except FileNotFoundError:
            self.error_msg = "mpv not found in PATH"
            self.proc = None
        except Exception as e:
            self.error_msg = f"launch failed: {type(e).__name__}: {e}"
            self.proc = None
        finally:
            # We don't need our handle to the log file — child has it.
            if log_fd is not subprocess.DEVNULL:
                try:
                    log_fd.close()  # type: ignore[union-attr]
                except Exception:
                    pass

    def _read_mpv_error(self) -> str:
        try:
            with open(MPV_LOG, "r") as f:
                tail = [ln.strip() for ln in f.read().strip().splitlines() if ln.strip()]
            if tail:
                return tail[-1][:80]
        except Exception:
            pass
        return ""

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
        # Check if process finished. If it died within 2s of launch we
        # treat that as a failure and pull the reason from the log so
        # the user sees an actionable error instead of a black screen.
        if self.playing_file and self.proc:
            rc = self.proc.poll()
            if rc is not None:
                quick_exit = (time.time() - self._launch_time) < 2.0
                if rc != 0 or quick_exit:
                    self.error_msg = (
                        self._read_mpv_error()
                        or f"mpv exited (code {rc})"
                    )
                    print(f"[media] mpv error: {self.error_msg}")
                    self.proc = None
                else:
                    print("[media] Playback finished naturally")
                    self.playing_file = None
                    self.proc = None
                    self.error_msg = None

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
                
                if self.proc:
                    msg = "Playing in background... Press A or B to return."
                    color = theme.FG_DIM
                else:
                    err = self.error_msg or "Player failed to start."
                    msg = f"ERROR: {err}  (B to return)"
                    color = theme.ERR
                hint = self.hint_font.render(msg, True, color)
                # Truncate if too wide to fit
                max_w = theme.SCREEN_W - 2 * theme.PADDING
                if hint.get_width() > max_w:
                    truncated = msg
                    while truncated and self.hint_font.size(truncated)[0] > max_w:
                        truncated = truncated[:-1]
                    hint = self.hint_font.render(truncated, True, color)
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
