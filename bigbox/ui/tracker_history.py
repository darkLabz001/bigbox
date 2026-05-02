"""Tracker History view — long-term "is anything following me" log.

Reads ``loot/tracker_history.jsonl`` (appended to by every detection
in TrackerDetector) and shows the per-MAC suspicion ranking. Useful
once you've used the live Trackers view across a few days.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pygame

from bigbox import theme, tracker_history
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


class TrackerHistoryView:
    def __init__(self) -> None:
        self.dismissed = False
        self.scroll = 0
        self.text = "Loading…"
        self.reports: list = []

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.hint_font = pygame.font.Font(None, theme.FS_SMALL)
        self.mono_font = pygame.font.Font(None, 18)

        threading.Thread(target=self._load, daemon=True).start()

    def _load(self) -> None:
        try:
            self.reports = tracker_history.analyse()
            self.text = tracker_history.render_text(self.reports)
        except Exception as e:
            self.text = f"Error loading history:\n{e}"

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.UP:
            self.scroll = max(0, self.scroll - 60)
        elif ev.button is Button.DOWN:
            self.scroll += 60
        elif ev.button is Button.A:
            self.text = "Loading…"
            threading.Thread(target=self._load, daemon=True).start()
        elif ev.button is Button.X:
            # Share the analysis dump via webhook.
            from bigbox import webhooks
            from datetime import datetime
            from pathlib import Path

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = Path(f"/tmp/bigbox-tracker-history-{ts}.txt")
            try:
                out.write_text(self.text)
            except Exception as e:
                ctx.toast(f"write failed: {e}")
                return

            def _send():
                ok, msg = webhooks.send_file(str(out))
                ctx.toast(msg if ok else f"failed: {msg}")
            threading.Thread(target=_send, daemon=True).start()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)

        title_text = "TRACKER HISTORY"
        if self.reports:
            title_text = f"TRACKER HISTORY · {len(self.reports)} flagged"
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
                if line.startswith("  ["):
                    # Score-prefixed rows: color by suspicion magnitude.
                    try:
                        score = int(line.split("[", 1)[1].split("]", 1)[0])
                    except Exception:
                        score = 0
                    if score >= 9:
                        color = theme.ERR
                    elif score >= 4:
                        color = theme.WARN
                    else:
                        color = theme.FG
                ls = self.mono_font.render(line[:110], True, color)
                surf.blit(ls, (x, y))
            y += line_h

        hint_text = "UP/DOWN: Scroll   A: Refresh   X: Send Report   B: Back"
        hint = self.hint_font.render(hint_text, True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
