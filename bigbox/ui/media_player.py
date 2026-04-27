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

# The two subfolders we always show. Web /upload validates against this
# same set so a bad form post can't escape into the parent directory.
CATEGORIES = ("movies", "tv")

PHASE_CATEGORY = "category"
PHASE_FILES = "files"
PHASE_PLAYING = "playing"
PHASE_RESULT = "result"


class MediaPlayerView:
    def __init__(self, media_dir: str = "media") -> None:
        self.media_dir = media_dir
        self.dismissed = False

        # phase: which screen is showing right now
        self.phase = PHASE_CATEGORY

        # category screen state
        self.categories: list[tuple[str, str, int]] = []  # (label, subdir, count)
        self.cat_cursor = 0

        # files screen state
        self.current_category: str | None = None  # subdir name or "" for root
        self.list: ScrollList = ScrollList([])

        # playback state
        self.playing_file: str | None = None        # display name (basename)
        self.playing_relpath: str | None = None     # path under media_dir
        self.proc: subprocess.Popen | None = None
        self.error_msg: str | None = None
        self._launch_time: float = 0.0

        # result screen state — held until user dismisses
        self.last_result: list[str] | None = None
        self.last_result_rc: int | None = None
        self.last_played: str | None = None

        # Always create the canonical subfolders so they show up in the
        # category list even if they're empty.
        self._ensure_dirs()
        self._refresh_categories()

        # Cache fonts so we don't re-load every frame
        try:
            self.title_font = pygame.font.Font(None, theme.FS_TITLE)
            self.body_font = pygame.font.Font(None, theme.FS_BODY)
            self.hint_font = pygame.font.Font(None, theme.FS_SMALL)
            self.play_font = pygame.font.Font(None, 64)
        except Exception as e:
            print(f"[media] Font init error: {e}")
            self.title_font = self.body_font = self.hint_font = self.play_font = \
                pygame.font.SysFont("monospace", 20)

    # ---------- filesystem ----------
    def _ensure_dirs(self) -> None:
        try:
            if not os.path.exists(self.media_dir):
                os.makedirs(self.media_dir)
        except Exception as e:
            print(f"[media] mkdir {self.media_dir}: {e}")
        for sub in CATEGORIES:
            try:
                p = os.path.join(self.media_dir, sub)
                if not os.path.exists(p):
                    os.makedirs(p)
            except Exception as e:
                print(f"[media] mkdir {sub}: {e}")

    def _list_files(self, subdir: str) -> list[str]:
        """Files (not dirs) directly inside media_dir/subdir. subdir="" for root."""
        path = os.path.join(self.media_dir, subdir) if subdir else self.media_dir
        if not os.path.isdir(path):
            return []
        try:
            entries = sorted(os.listdir(path))
        except OSError:
            return []
        out = []
        for f in entries:
            full = os.path.join(path, f)
            if os.path.isfile(full):
                # Hide loose root files when listing root *if* they live in a
                # tracked subdir — but here subdir is "" so we just take all.
                out.append(f)
        return out

    def _refresh_categories(self) -> None:
        cats: list[tuple[str, str, int]] = []
        for sub in CATEGORIES:
            count = len(self._list_files(sub))
            cats.append((sub.upper(), sub, count))
        # "Other" only if there are loose files in the media root.
        loose = self._list_files("")
        # Filter out subdir names themselves (they aren't files anyway, but
        # be defensive).
        loose = [f for f in loose if f not in CATEGORIES]
        if loose:
            cats.append(("OTHER", "", len(loose)))
        self.categories = cats
        if self.cat_cursor >= len(cats):
            self.cat_cursor = max(0, len(cats) - 1)

    def _build_file_list(self) -> ScrollList:
        sub = self.current_category or ""
        files = self._list_files(sub)
        actions: list[Action] = []
        for f in files:
            relpath = os.path.join(sub, f) if sub else f

            def make_handler(rp: str):
                return lambda ctx: self._play(rp)

            actions.append(Action(f, make_handler(relpath)))
        if not actions:
            actions.append(Action("[ Empty ]", None, "Upload via Web UI"))
        return ScrollList(actions)

    def refresh(self) -> None:
        """External refresh hook — called from /upload after a web upload."""
        self._refresh_categories()
        if self.phase == PHASE_FILES:
            self.list = self._build_file_list()

    # Backward-compat: server.py used to call _refresh_list().
    def _refresh_list(self) -> ScrollList:
        self._refresh_categories()
        if self.phase == PHASE_FILES:
            self.list = self._build_file_list()
        return self.list

    # ---------- playback ----------
    def _play(self, relpath: str) -> None:
        self.playing_relpath = relpath
        self.playing_file = os.path.basename(relpath)
        self.error_msg = None
        self.proc = None
        full_path = os.path.abspath(os.path.join(self.media_dir, relpath))

        print(f"[media] Playing: {full_path}")

        if not shutil.which("mpv"):
            self.error_msg = "mpv not installed (apt install mpv)"
            print(f"[media] {self.error_msg}")
            self.phase = PHASE_PLAYING
            return
        if not os.path.exists(full_path):
            self.error_msg = f"file not found: {full_path}"
            print(f"[media] {self.error_msg}")
            self.phase = PHASE_PLAYING
            return

        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XAUTHORITY", "/root/.Xauthority")

        try:
            log_fd: int | object = open(MPV_LOG, "w")
        except Exception:
            log_fd = subprocess.DEVNULL

        # GamePi43 image is fbdev (no /dev/dri, no Xvideo, no Vulkan, no
        # VDPAU). --vo=x11 is the only software output that actually puts
        # pixels on this screen; tested live on the device.
        # Boost volume to max at system level before starting mpv. Pin to
        # card 1 (BCM2835 Headphones — the 3.5mm jack the GamePi43 speaker
        # is wired to); the unscoped form was setting HDMI volume instead.
        try:
            subprocess.run(
                ["amixer", "-c", "1", "sset", "PCM", "100%"],
                capture_output=True,
            )
        except Exception:
            pass

        cmd = [
            "mpv",
            "--vo=x11",
            "--fs",
            "--cursor-autohide=always",
            # Pin mpv's audio device to the Headphones card explicitly. This
            # is defense-in-depth against /etc/asound.conf going missing on
            # a re-flash — without this the stock ALSA default lands on
            # HDMI (card 0, silent on this hardware).
            "--ao=alsa",
            "--audio-device=alsa/default:CARD=Headphones",
            "--volume=130",
            "--af=loudnorm",
            "--no-osc",
            "--no-input-default-bindings",
            "--msg-level=all=warn",
            full_path,
        ]
        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                env=env,
            )
            self._launch_time = time.time()
            self.phase = PHASE_PLAYING
        except FileNotFoundError:
            self.error_msg = "mpv not found in PATH"
            self.proc = None
            self.phase = PHASE_PLAYING
        except Exception as e:
            self.error_msg = f"launch failed: {type(e).__name__}: {e}"
            self.proc = None
            self.phase = PHASE_PLAYING
        finally:
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
        self.playing_relpath = None
        self.last_result = None
        self.last_result_rc = None
        self.last_played = None
        self.error_msg = None
        # On explicit stop, return to the file list of the current category.
        self.phase = PHASE_FILES if self.current_category is not None else PHASE_CATEGORY

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        try:
            if not ev.pressed:
                return

            if self.phase == PHASE_RESULT:
                if ev.button in (Button.A, Button.B, Button.START, Button.SELECT):
                    self.last_result = None
                    self.last_result_rc = None
                    self.last_played = None
                    self.phase = (PHASE_FILES
                                  if self.current_category is not None
                                  else PHASE_CATEGORY)
                return

            if self.phase == PHASE_PLAYING:
                if ev.button is Button.B:
                    self._stop()
                elif ev.button in (Button.A, Button.START, Button.SELECT):
                    self._stop()
                return

            if self.phase == PHASE_CATEGORY:
                if ev.button is Button.B:
                    self.dismissed = True
                elif ev.button is Button.UP and self.categories:
                    self.cat_cursor = (self.cat_cursor - 1) % len(self.categories)
                elif ev.button is Button.DOWN and self.categories:
                    self.cat_cursor = (self.cat_cursor + 1) % len(self.categories)
                elif ev.button is Button.A and self.categories:
                    _, sub, _ = self.categories[self.cat_cursor]
                    self.current_category = sub
                    self.list = self._build_file_list()
                    self.phase = PHASE_FILES
                return

            if self.phase == PHASE_FILES:
                if ev.button is Button.B:
                    self.current_category = None
                    self._refresh_categories()
                    self.phase = PHASE_CATEGORY
                    return
                action = self.list.handle(ev)
                if action and action.handler:
                    action.handler(ctx)
                return
        except Exception as e:
            print(f"[media] Handle error: {e}")

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        # Did mpv exit? Pull its log onto the result screen.
        if self.phase == PHASE_PLAYING and self.proc:
            rc = self.proc.poll()
            if rc is not None:
                self.last_result = self._read_mpv_log_tail(8) or [
                    f"mpv exited with code {rc} (no log output)"
                ]
                self.last_result_rc = rc
                self.last_played = self.playing_file
                self.proc = None
                self.playing_file = None
                self.playing_relpath = None
                self.error_msg = None
                self.phase = PHASE_RESULT

        try:
            surf.fill(theme.BG)

            head_h = 60
            pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
            pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                             (theme.SCREEN_W, head_h - 1), 2)

            if self.phase == PHASE_RESULT:
                title_text = "PLAYBACK RESULT"
            elif self.phase == PHASE_PLAYING:
                title_text = f"PLAYING: {self.playing_file or ''}"
            elif self.phase == PHASE_FILES:
                title_text = f"MEDIA :: {(self.current_category or 'OTHER').upper()}"
            else:
                title_text = "MEDIA PLAYER"

            title = self.title_font.render(title_text, True, theme.ACCENT)
            surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

            if self.phase == PHASE_RESULT:
                self._render_result(surf, head_h)
            elif self.phase == PHASE_PLAYING:
                self._render_playing(surf, head_h)
            elif self.phase == PHASE_FILES:
                self._render_files(surf, head_h)
            else:
                self._render_categories(surf, head_h)
        except Exception as e:
            print(f"[media] Render error: {e}")

    def _render_categories(self, surf: pygame.Surface, head_h: int) -> None:
        if not self.categories:
            f = pygame.font.Font(None, 24)
            msg = f.render("No categories.", True, theme.FG_DIM)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2))
            return

        list_x = theme.PADDING
        list_y = head_h + theme.PADDING
        list_w = theme.SCREEN_W - 2 * theme.PADDING
        list_h = theme.SCREEN_H - head_h - 2 * theme.PADDING - 40
        pygame.draw.rect(surf, (5, 5, 10), (list_x, list_y, list_w, list_h))
        pygame.draw.rect(surf, theme.DIVIDER, (list_x, list_y, list_w, list_h), 1)

        row_h = 56
        f_main = pygame.font.Font(None, 32)
        f_meta = pygame.font.Font(None, 20)
        for i, (label, sub, count) in enumerate(self.categories):
            y = list_y + i * row_h
            if y + row_h > list_y + list_h:
                break
            rect = pygame.Rect(list_x + 4, y + 4, list_w - 8, row_h - 8)
            if i == self.cat_cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=6)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=6)
                color = theme.ACCENT
            else:
                color = theme.FG
            ls = f_main.render(label, True, color)
            surf.blit(ls, (rect.x + 14, rect.y + 8))
            cs = f_meta.render(f"{count} file{'s' if count != 1 else ''}",
                               True, theme.FG_DIM)
            surf.blit(cs, (rect.right - cs.get_width() - 14, rect.y + 18))

        hint = self.hint_font.render(
            "UP/DOWN: Navigate  A: Open  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

    def _render_files(self, surf: pygame.Surface, head_h: int) -> None:
        list_rect = pygame.Rect(
            theme.PADDING,
            head_h + theme.PADDING,
            theme.SCREEN_W - 2 * theme.PADDING,
            theme.SCREEN_H - head_h - 2 * theme.PADDING - 40,
        )
        self.list.render(surf, list_rect, self.body_font)
        hint = self.hint_font.render(
            "UP/DOWN: Navigate  A: Play  B: Categories", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

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

    def _render_result(self, surf: pygame.Surface, head_h: int) -> None:
        rc = self.last_result_rc if self.last_result_rc is not None else 0
        ok = (rc == 0)
        accent = theme.ACCENT if ok else theme.ERR

        sub_y = head_h + theme.PADDING
        sub = self.body_font.render(
            f"{self.last_played or ''}  (exit {rc})", True, accent)
        surf.blit(sub, (theme.PADDING, sub_y))

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
            while text and f.size(text)[0] > max_w:
                text = text[:-1]
            lc = (theme.ERR if any(k in raw.lower() for k in
                                   ("error", "fail", "cannot", "could not"))
                  else theme.FG_DIM)
            ls = f.render(text, True, lc)
            surf.blit(ls, (log_rect.x + 8, log_rect.y + 8 + i * line_h))

        hint = self.hint_font.render(
            "B: dismiss   /tmp/bigbox-mpv.log has full output",
            True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
