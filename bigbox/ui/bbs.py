"""Local BBS UI — stub for local bulletin board access."""
from __future__ import annotations
import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent

class BBSView:
    def __init__(self) -> None:
        self.dismissed = False

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if ev.pressed and ev.button is Button.B:
            self.dismissed = True

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        f = pygame.font.Font(None, 32)
        txt = f.render("LOCAL BBS :: OFFLINE COMM", True, theme.ACCENT)
        surf.blit(txt, (theme.PADDING, theme.PADDING))
        
        hint = pygame.font.Font(None, 24).render("B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
        
        msg = pygame.font.Font(None, 24).render("Server running on port 23 (Telnet)", True, theme.FG)
        surf.blit(msg, (theme.PADDING, 100))
