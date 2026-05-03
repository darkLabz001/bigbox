"""Dead Drop UI — Rogue AP based offline chat room."""
from __future__ import annotations

import os
import shutil
import threading
import time
from typing import TYPE_CHECKING, Optional

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.deaddrop import DeadDropServer
from bigbox.eviltwin import EvilTwinSession, iface_supports_ap

if TYPE_CHECKING:
    from bigbox.app import App


class DeadDropView:
    def __init__(self) -> None:
        self.dismissed = False
        self.session: Optional[EvilTwinSession] = None
        self.chat_server: Optional[DeadDropServer] = None
        
        self.iface = self._find_iface()
        self.ssid = "FREE_CHAT"
        self.phase = "SETUP" # SETUP, RUNNING, ERROR
        self.error_msg = ""
        
        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)

    def _find_iface(self) -> str:
        # Prefer wlan1 (external Alfa) if present, else wlan0
        for i in ("wlan1", "wlan0"):
            if os.path.exists(f"/sys/class/net/{i}"):
                return i
        return "wlan0"

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if self.phase == "SETUP":
            if ev.button is Button.B:
                self.dismissed = True
            elif ev.button is Button.A:
                self._start_session()
            elif ev.button is Button.X:
                ctx.get_input("Set SSID", self._on_ssid_done, initial=self.ssid)
        
        elif self.phase == "RUNNING":
            if ev.button is Button.B:
                self._stop_session()
                self.phase = "SETUP"
        
        elif self.phase == "ERROR":
            if ev.button in (Button.A, Button.B):
                self.phase = "SETUP"

    def _on_ssid_done(self, text: str | None):
        if text:
            self.ssid = text.strip()[:32] or "FREE_CHAT"

    def _start_session(self):
        self.session = EvilTwinSession(iface=self.iface, ssid=self.ssid, skip_portal=True)
        self.chat_server = DeadDropServer(ssid=self.ssid)
        
        ok, msg = self.session.start()
        if not ok:
            self.phase = "ERROR"
            self.error_msg = msg
        else:
            self.chat_server.start()
            self.phase = "RUNNING"

    def _stop_session(self):
        if self.chat_server:
            self.chat_server.stop()
        if self.session:
            self.session.stop()
        self.session = None
        self.chat_server = None

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        title = self.title_font.render("DEAD DROP :: OFFLINE CHAT", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        if self.phase == "SETUP":
            lines = [
                f"Interface: {self.iface}",
                f"SSID: {self.ssid}",
                "",
                "A: Start AP & Chat Room",
                "X: Change SSID",
                "B: Cancel"
            ]
            for i, ln in enumerate(lines):
                s = self.body_font.render(ln, True, theme.FG)
                surf.blit(s, (theme.PADDING, 100 + i*30))
        
        elif self.phase == "RUNNING":
            clients = self.session.clients_connected() if self.session else 0
            msgs = len(self.chat_server.messages) if self.chat_server else 0
            
            lines = [
                "STATUS: ACTIVE",
                f"SSID: {self.ssid}",
                f"Clients: {clients}",
                f"Messages: {msgs}",
                "",
                "B: Stop Session"
            ]
            for i, ln in enumerate(lines):
                s = self.body_font.render(ln, True, theme.FG)
                surf.blit(s, (theme.PADDING, 100 + i*30))
            
            if int(time.time()) % 2:
                pygame.draw.circle(surf, theme.ACCENT, (theme.SCREEN_W - 50, 50), 10)

        elif self.phase == "ERROR":
            err = self.body_font.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err, (theme.PADDING, 100))
            hint = self.body_font.render("Press A or B to return", True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, 140))
