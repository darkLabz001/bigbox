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
        # After mpv exits we hold a result screen so the user always sees
        # what happened. Cleared by pressing B.
        self.last_result: list[str] | None = None
        self.last_result_rc: int | None = None
        self.last_played: str | None = None
        
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

        # The GamePi43 image is fbdev (no /dev/dri, no Xvideo, no Vulkan,
        # no VDPAU), so all of mpv's accelerated video outputs fail and
        # the autoselect ends up on a backend that decodes silently with
        # no visible window. --vo=x11 is the software X11 path; it works
        # on every fbdev+xinit setup, just with a "legacy VO" warning.
        cmd = [
            "mpv",
            "--vo=x11",                      # software X11 — fbdev compatible
            "--fs",                          # fullscreen
            "--cursor-autohide=always",
            "--ao=alsa,pulse,null",          # alsa first, fall back gracefully
            "--no-osc",                      # bigbox owns the controls
            "--no-input-default-bindings",   # don't react to keyboard
            "--msg-level=all=warn",          # warn+ (skip per-frame status)
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

    def _read_mpv_log_tail(self, n: int = 8) -> list[str]:
        try:
            with open(MPV_LOG, "r") as f:
                lines = [ln.rstrip() for ln in f.readlines() if ln.strip()]
            return lines[-n:] if lines else []
        except Exception:
            return []

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
        # When the user explicitly stops, skip the result screen.
        self.last_result = None
        self.last_result_rc = None
        self.last_played = None
        self.error_msg = None

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        try:
            if not ev.pressed:
                return

            # Result screen: any button (especially B/A) clears it.
            if self.last_result is not None:
                if ev.button in (Button.A, Button.B, Button.START, Button.SELECT):
                    self.last_result = None
                    self.last_result_rc = None
                    self.last_played = None
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
        # Did mpv exit? Pull its log onto a result screen the user has
        # to dismiss with B. Always — regardless of exit code — so we
        # never silently bounce back to the menu after a failure.
        if self.playing_file and self.proc:
            rc = self.proc.poll()
            if rc is not None:
                self.last_result = self._read_mpv_log_tail(8) or [
                    f"mpv exited with code {rc} (no log output)"
                ]
                self.last_result_rc = rc
                self.last_played = self.playing_file
                self.proc = None
                self.playing_file = None
                self.error_msg = None

        try:
            surf.fill(theme.BG)

            # Header
            head_h = 60
            pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
            pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                             (theme.SCREEN_W, head_h - 1), 2)

            if self.last_result is not None:
                title_text = "PLAYBACK RESULT"
            elif self.playing_file:
                title_text = f"PLAYING: {self.playing_file}"
            else:
                title_text = "MEDIA PLAYER"

            title = self.title_font.render(title_text, True, theme.ACCENT)
            surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

            if self.last_result is not None:
                self._render_result(surf, head_h)
            elif self.playing_file:
                self._render_playing(surf, head_h)
            else:
                self._render_list(surf, head_h)
        except Exception as e:
            print(f"[media] Render error: {e}")

    def _render_playing(self, surf: pygame.Surface, head_h: int) -> None:
        center_x, center_y = theme.SCREEN_W // 2, theme.SCREEN_H // 2

        box_w, box_h = 600, 340
        box = pygame.Rect(center_x - box_w // 2,
                          center_y - box_h // 2 + 20, box_w, box_h)
        pygame.draw.rect(surf, (0, 0, 0), box)
        pygame.draw.rect(surf, theme.ACCENT_DIM, box, 2)

        icon = self.play_font.render("▶", True, theme.ACCENT)
        surf.blit(icon, (center_x - icon.get_width() // 2,
                         center_y - icon.get_height() // 2 + 20))

        if self.proc:
            msg = "Playing in background... Press A or B to return."
            color = theme.FG_DIM
        else:
            err = self.error_msg or "Player failed to start."
            msg = f"ERROR: {err}  (B to return)"
            color = theme.ERR
        hint = self.hint_font.render(msg, True, color)
        max_w = theme.SCREEN_W - 2 * theme.PADDING
        if hint.get_width() > max_w:
            truncated = msg
            while truncated and self.hint_font.size(truncated)[0] > max_w:
                truncated = truncated[:-1]
            hint = self.hint_font.render(truncated, True, color)
        surf.blit(hint, (center_x - hint.get_width() // 2, box.bottom + 20))

    def _render_list(self, surf: pygame.Surface, head_h: int) -> None:
        list_rect = pygame.Rect(
            theme.PADDING,
            head_h + theme.PADDING,
            theme.SCREEN_W - 2 * theme.PADDING,
            theme.SCREEN_H - head_h - 2 * theme.PADDING - 40,
        )
        self.list.render(surf, list_rect, self.body_font)
        hint = self.hint_font.render(
            "UP/DOWN: Navigate  A: Play  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

    def _render_result(self, surf: pygame.Surface, head_h: int) -> None:
        rc = self.last_result_rc if self.last_result_rc is not None else 0
        ok = (rc == 0)
        accent = theme.ACCENT if ok else theme.ERR

        # Subtitle: file + exit code
        sub_y = head_h + theme.PADDING
        sub = self.body_font.render(
            f"{self.last_played or ''}  (exit {rc})", True, accent)
        surf.blit(sub, (theme.PADDING, sub_y))

        # Log box
        log_y = sub_y + sub.get_height() + 8
        log_h = theme.SCREEN_H - log_y - 40
        log_rect = pygame.Rect(theme.PADDING, log_y,
                               theme.SCREEN_W - 2 * theme.PADDING, log_h)
        pygame.draw.rect(surf, (0, 0, 0), log_rect)
        pygame.draw.rect(surf, theme.DIVIDER, log_rect, 1)

        f = pygame.font.Font(None, 18)
        line_h = f.get_linesize()
        max_lines = max(1, (log_rect.height - 16) // line_h)
        lines = (self.last_result or [])[-max_lines:]

        max_w = log_rect.width - 16
        for i, raw in enumerate(lines):
            text = raw
            # Truncate per line
            while text and f.size(text)[0] > max_w:
                text = text[:-1]
            # Color: red if it looks like an error, dim otherwise
            lc = (theme.ERR if any(k in raw.lower() for k in
                                   ("error", "fail", "cannot", "could not"))
                  else theme.FG_DIM)
            ls = f.render(text, True, lc)
            surf.blit(ls, (log_rect.x + 8, log_rect.y + 8 + i * line_h))

        hint = self.hint_font.render(
            "B: dismiss   /tmp/bigbox-mpv.log has full output",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
