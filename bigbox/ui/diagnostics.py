"""Diagnostics view — read recent crashes from the systemd journal so
the user can see them on-device. X sends the dump to the configured
webhook for easy off-device sharing.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pygame

from bigbox import diagnostics, theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


class DiagnosticsView:
    def __init__(self) -> None:
        self.dismissed = False
        self.scroll = 0
        self.text = "Loading…"
        self.tracebacks: list = []
        self.share_status = ""

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.hint_font = pygame.font.Font(None, theme.FS_SMALL)
        self.mono_font = pygame.font.Font(None, 18)

        # Loading the journal can take a couple hundred ms; do it off
        # the main thread so the view appears instantly.
        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        tbs = diagnostics.recent_tracebacks(limit=20)
        self.tracebacks = tbs
        self.text = diagnostics.render_text(tbs)

    def _share(self) -> None:
        if not self.text or self.text == "Loading…":
            self.share_status = "nothing to send"
            return
        from bigbox import webhooks
        from datetime import datetime

        from pathlib import Path
        # Dump to a temp file and reuse webhooks.send_file for the
        # multipart upload semantics the existing webhook code knows.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path(f"/tmp/bigbox-tracebacks-{ts}.txt")
        try:
            out.write_text(self.text)
        except Exception as e:
            self.share_status = f"write failed: {e}"
            return

        def _worker():
            try:
                ok, msg = webhooks.send_file(str(out))
                self.share_status = msg if ok else f"failed: {msg}"
            except Exception as e:
                self.share_status = f"webhook error: {e}"

        self.share_status = "sending..."
        threading.Thread(target=_worker, daemon=True).start()

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.UP:
            self.scroll = max(0, self.scroll - 60)
        elif ev.button is Button.DOWN:
            self.scroll += 60
        elif ev.button is Button.X:
            self._share()
        elif ev.button is Button.A:
            # Refresh
            self.text = "Loading…"
            self.share_status = ""
            threading.Thread(target=self._load, daemon=True).start()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title_text = "DIAGNOSTICS"
        if self.tracebacks:
            title_text = f"DIAGNOSTICS · {len(self.tracebacks)} traceback(s)"
        title = self.title_font.render(title_text, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        body = pygame.Rect(theme.PADDING, head_h + 8,
                           theme.SCREEN_W - 2 * theme.PADDING,
                           theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (5, 5, 10), body)
        pygame.draw.rect(surf, theme.DIVIDER, body, 1)

        line_h = 20
        x = body.x + 8
        y = body.y + 6 - self.scroll
        for line in self.text.splitlines():
            if y > body.bottom:
                break
            if y + line_h >= body.y:
                color = theme.FG_DIM
                if line.startswith("---"):
                    color = theme.ACCENT
                elif "Error" in line or "Exception" in line:
                    color = theme.ERR
                ls = self.mono_font.render(line[:100], True, color)
                surf.blit(ls, (x, y))
            y += line_h

        hint_text = "UP/DOWN: Scroll   A: Refresh   X: Send via Webhook   B: Back"
        if self.share_status:
            hint_text = self.share_status + "    " + hint_text
        hint = self.hint_font.render(hint_text, True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
