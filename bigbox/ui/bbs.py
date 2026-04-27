"""Local BBS UI — manages the telnet BBS server."""
from __future__ import annotations
import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.bbs_server import BBSServer
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bigbox.app import App

class BBSView:
    def __init__(self) -> None:
        self.dismissed = False
        self.server: Optional[BBSServer] = None
        self.phase = "SETUP" # SETUP, RUNNING, ERROR
        self.error_msg = ""
        self.port = 2323
        
        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if self.phase == "SETUP":
            if ev.button is Button.B:
                self.dismissed = True
            elif ev.button is Button.A:
                self._start_server()
        
        elif self.phase == "RUNNING":
            if ev.button is Button.B:
                self._stop_server()
                self.phase = "SETUP"
        
        elif self.phase == "ERROR":
            if ev.button in (Button.A, Button.B):
                self.phase = "SETUP"

    def _start_server(self):
        self.server = BBSServer(port=self.port)
        ok, msg = self.server.start()
        if ok:
            self.phase = "RUNNING"
        else:
            self.phase = "ERROR"
            self.error_msg = msg

    def _stop_server(self):
        if self.server:
            self.server.stop()
        self.server = None

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        title = self.title_font.render("LOCAL BBS :: TELNET SERVER", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        if self.phase == "SETUP":
            lines = [
                f"Port: {self.port}",
                "",
                "A: Start BBS Server",
                "B: Cancel"
            ]
            for i, ln in enumerate(lines):
                s = self.body_font.render(ln, True, theme.FG)
                surf.blit(s, (theme.PADDING, 100 + i*30))
        
        elif self.phase == "RUNNING":
            clients = len(self.server.clients) if self.server else 0
            msgs = len(self.server.history) if self.server else 0
            
            lines = [
                "STATUS: ACTIVE",
                f"Listening on port {self.port}",
                f"Connected Clients: {clients}",
                f"History size: {msgs}",
                "",
                "B: Stop Server"
            ]
            for i, ln in enumerate(lines):
                s = self.body_font.render(ln, True, theme.FG)
                surf.blit(s, (theme.PADDING, 100 + i*30))

        elif self.phase == "ERROR":
            err = self.body_font.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err, (theme.PADDING, 100))
            hint = self.body_font.render("Press A or B to return", True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, 140))
