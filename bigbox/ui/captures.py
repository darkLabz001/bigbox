"""View Captures — browse and act on screenshots/recordings produced
by the hotkey menu (Y for screenshot, Record Screen toggle).

Self-contained payload: lives outside media_player so the captures
flow is independent of movie/TV playback.
"""
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


PHASE_FILES = "files"
PHASE_VIEW_IMAGE = "view_image"
PHASE_ACTION_MENU = "action_menu"


class CapturesView:
    # Primary capture directory — app.py's _take_screenshot and
    # _toggle_screen_record both write here. The legacy screenshots/
    # and recordings/ directories are still scanned so older files
    # remain visible after the consolidation.
    CAPTURES_DIR = "media/captures"

    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_FILES

        self.dirs = [self.CAPTURES_DIR, "screenshots", "recordings"]
        self.list = ScrollList([])
        self.action_list: ScrollList | None = None
        self.target_path: str | None = None

        self.current_img: pygame.Surface | None = None
        self.current_fname: str | None = None

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.hint_font = pygame.font.Font(None, theme.FS_SMALL)

        self._refresh_list()

    def _refresh_list(self) -> None:
        actions = []
        for dname in self.dirs:
            if not os.path.exists(dname):
                continue
            try:
                files = [f for f in os.listdir(dname) if os.path.isfile(os.path.join(dname, f))]
                files.sort(key=lambda f: os.path.getmtime(os.path.join(dname, f)), reverse=True)

                for f in files:
                    full = os.path.join(dname, f)
                    def make_handler(path):
                        return lambda ctx: self._open_action_menu(path)
                    size = os.path.getsize(full) / 1024
                    actions.append(Action(f, make_handler(full), f"{dname.upper()} :: {size:.1f}KB"))
            except Exception:
                pass

        if not actions:
            actions.append(Action("[ No captures found ]", None))
        self.list = ScrollList(actions)

    def _open_action_menu(self, path: str) -> None:
        self.target_path = path
        actions = [
            Action("View / Play", lambda ctx: self._open_file(path)),
            Action("Delete", lambda ctx: self._delete_file(path)),
            Action("Share via Webhook", lambda ctx: self._send_webhook(path)),
        ]
        self.action_list = ScrollList(actions)
        self.phase = PHASE_ACTION_MENU

    def _delete_file(self, path: str) -> None:
        try:
            os.remove(path)
            self._refresh_list()
            self.phase = PHASE_FILES
        except Exception as e:
            print(f"[captures] Delete failed: {e}")

    def _send_webhook(self, path: str) -> None:
        from bigbox import webhooks
        import threading

        def _worker():
            ok, msg = webhooks.send_file(path)
            print(f"[captures] Webhook: {msg}")
            self.phase = PHASE_FILES

        threading.Thread(target=_worker, daemon=True).start()
        self.phase = PHASE_FILES

    def _open_file(self, path: str) -> None:
        if path.lower().endswith((".mp4", ".avi", ".mkv", ".mjpg")):
            self._play_video(path)
        elif path.lower().endswith((".png", ".jpg", ".jpeg")):
            try:
                raw = pygame.image.load(path)
                # Pre-scale once on load — re-scaling every frame at 30fps
                # froze the UI on a Pi 4.
                head_h = 60
                sw, sh = theme.SCREEN_W, theme.SCREEN_H - head_h - 40
                iw, ih = raw.get_size()
                scale = min(sw / iw, sh / ih)
                nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
                self.current_img = pygame.transform.smoothscale(raw, (nw, nh))
                self.current_fname = os.path.basename(path)
                self.phase = PHASE_VIEW_IMAGE
            except Exception as e:
                print(f"[captures] Error loading image: {e}")

    def _play_video(self, path: str) -> None:
        env = os.environ.copy()
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XAUTHORITY", "/root/.Xauthority")
        cmd = [
            "mpv",
            "--vo=x11",
            "--fs",
            "--no-osc",
            "--ao=alsa,pulse,null",
            "--no-input-default-bindings",
            "--audio-display=no",
            "--really-quiet",
            path,
        ]
        try:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except Exception as e:
            print(f"[captures] mpv launch failed: {e}")

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return

        if self.phase == PHASE_VIEW_IMAGE:
            if ev.button in (Button.B, Button.A):
                self.phase = PHASE_FILES
                self.current_img = None
            return

        if self.phase == PHASE_ACTION_MENU and self.action_list:
            if ev.button is Button.B:
                self.phase = PHASE_FILES
                return
            action = self.action_list.handle(ev)
            if action and action.handler:
                action.handler(ctx)
            return

        if ev.button is Button.B:
            self.dismissed = True
            return

        action = self.list.handle(ev)
        if action and action.handler:
            action.handler(ctx)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1), (theme.SCREEN_W, head_h - 1), 2)

        if self.phase == PHASE_VIEW_IMAGE:
            title_text = f"VIEW :: {self.current_fname}"
        elif self.phase == PHASE_ACTION_MENU:
            title_text = f"ACTIONS :: {os.path.basename(self.target_path or '')}"
        else:
            title_text = "SYSTEM CAPTURES"

        title = self.title_font.render(title_text, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        if self.phase == PHASE_VIEW_IMAGE and self.current_img:
            nw, nh = self.current_img.get_size()
            sh = theme.SCREEN_H - head_h - 40
            x = (theme.SCREEN_W - nw) // 2
            y = head_h + (sh - nh) // 2
            surf.blit(self.current_img, (x, y))

            hint = self.hint_font.render("B: Back", True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
        elif self.phase == PHASE_ACTION_MENU and self.action_list:
            list_rect = pygame.Rect(theme.PADDING, head_h + theme.PADDING,
                                    theme.SCREEN_W - 2 * theme.PADDING,
                                    theme.SCREEN_H - head_h - 2 * theme.PADDING - 40)
            self.list.render(surf, list_rect, self.body_font)

            overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            surf.blit(overlay, (0, 0))

            menu_rect = pygame.Rect(theme.SCREEN_W // 4, theme.SCREEN_H // 4,
                                    theme.SCREEN_W // 2, theme.SCREEN_H // 2)
            pygame.draw.rect(surf, theme.BG_ALT, menu_rect, border_radius=8)
            pygame.draw.rect(surf, theme.ACCENT, menu_rect, 2, border_radius=8)

            header = self.body_font.render("CHOOSE ACTION", True, theme.ACCENT)
            surf.blit(header, (menu_rect.centerx - header.get_width() // 2, menu_rect.y + 10))

            act_rect = pygame.Rect(menu_rect.x + 10, menu_rect.y + 40,
                                   menu_rect.width - 20, menu_rect.height - 60)
            self.action_list.render(surf, act_rect, self.body_font)

            hint = self.hint_font.render("UP/DOWN: Select  A: Execute  B: Cancel",
                                         True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
        else:
            list_rect = pygame.Rect(theme.PADDING, head_h + theme.PADDING,
                                    theme.SCREEN_W - 2 * theme.PADDING,
                                    theme.SCREEN_H - head_h - 2 * theme.PADDING - 40)
            self.list.render(surf, list_rect, self.body_font)

            hint = self.hint_font.render("UP/DOWN: Navigate  A: Actions  B: Back",
                                         True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
