"""Onion Chat UI — stub for Tor-routed IRC."""
from __future__ import annotations
import os
import shutil
import subprocess
import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigbox.app import App

class OnionChatView:
    def __init__(self) -> None:
        self.dismissed = False
        self.tor_running = self._check_tor()
        
        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)

    def _check_tor(self) -> bool:
        rc = subprocess.run(["systemctl", "is-active", "tor"], capture_output=True).returncode
        return rc == 0

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.A:
            if not self.tor_running:
                ctx.show_result("Tor Error", "Tor service is not running.\nInstall/start with:\nsudo apt install tor\nsudo systemctl start tor")
            else:
                # Stub for launching a terminal-based IRC client via torify
                # This requires an actual terminal/TTY or a more complex PTY wrapper
                ctx.show_result("Onion IRC", "Tor is ACTIVE.\nConnect to Freenode/Libera via Torify:\n\n1. Drop to TTY (Ctrl-Alt-F2)\n2. Run: torify irssi -c irc.libera.chat")

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        title = self.title_font.render("ONION IRC :: ANONYMOUS", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, theme.PADDING))

        status = "TOR ACTIVE" if self.tor_running else "TOR OFFLINE"
        color = theme.ACCENT if self.tor_running else theme.ERR
        s_surf = self.body_font.render(f"STATUS: {status}", True, color)
        surf.blit(s_surf, (theme.PADDING, 80))

        lines = [
            "Route your chat traffic through the Tor network.",
            "",
            "A: Check connection / Instructions",
            "B: Back"
        ]
        for i, ln in enumerate(lines):
            ls = self.body_font.render(ln, True, theme.FG)
            surf.blit(ls, (theme.PADDING, 150 + i*30))
