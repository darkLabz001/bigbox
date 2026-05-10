"""Pager Sniffer — SDR view.

Uses rtl_fm and multimon-ng to decode POCSAG/FLEX pager messages.
"""
from __future__ import annotations

import time
import threading
from collections import deque

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.sdr import get_pager
from bigbox.ui.section import SectionContext


class PagerView:
    def __init__(self) -> None:
        self.dismissed = False
        self.status_msg = "Press A to start pager sniffer"
        self.running = False
        self.sdr = get_pager()
        self.messages = deque(maxlen=20)
        self.last_update = 0

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._shutdown()
            return

        if ev.button is Button.A:
            if not self.running:
                if self.sdr.start():
                    self.running = True
                    self.status_msg = "Listening on 152.0MHz..."
                    threading.Thread(target=self._reader, daemon=True).start()
                else:
                    self.status_msg = "Error: rtl_fm or multimon-ng missing"
            else:
                self.sdr.stop()
                self.running = False
                self.status_msg = "Stopped"

    def _reader(self) -> None:
        while self.running and not self.dismissed:
            line = self.sdr.read_line()
            if line:
                self.messages.append(line)
            else:
                time.sleep(0.1)

    def _shutdown(self) -> None:
        self.running = False
        self.sdr.stop()
        self.dismissed = True

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("SDR :: PAGER SNIFFER", True, theme.ACCENT), (20, 20))
        
        y = 60
        f_small = pygame.font.Font(None, 20)
        
        for msg in list(self.messages)[-10:]:
            # Clean up raw output a bit
            clean_msg = msg.strip()
            if clean_msg:
                surf.blit(f_small.render(clean_msg[:80], True, theme.FG), (20, y))
                y += 22

        if not self.messages:
            surf.blit(f_small.render("No messages captured yet.", True, theme.FG_DIM), (20, y))

        status_surf = f_small.render(self.status_msg, True, theme.ACCENT)
        surf.blit(status_surf, (20, theme.SCREEN_H - 40))
