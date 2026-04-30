"""Wargames (Global Thermonuclear War) — A tactical terminal simulation.
Designed for bigbox (800x480) with ButtonEvent support.
"""
from __future__ import annotations
import pygame
import time
import random
from bigbox import theme
from bigbox.events import Button, ButtonEvent

class Wargames:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.running = True
        self.font_main = pygame.font.Font(None, 24)
        self.font_big = pygame.font.Font(None, 48)
        self.font_mono = pygame.font.SysFont("monospace", 20)
        
        self.history = [
            "GREETINGS PROFESSOR FALKEN.",
            "",
            "SHALL WE PLAY A GAME?",
            "",
            "[A] GLOBAL THERMONUCLEAR WAR",
            "[B] CHESS"
        ]
        self.phase = "GREETING"
        self.timer = 0
        self.defcon = 5
        self.targets = ["NEW YORK", "MOSCOW", "LONDON", "PARIS", "TOKYO", "LOS ANGELES"]
        self.launched = False

    def handle(self, ev: ButtonEvent):
        if not ev.pressed: return
        
        if ev.button is Button.B and self.phase == "GREETING":
            self.history.append("> CHESS")
            self.history.append("A STRANGE GAME.")
            self.history.append("THE ONLY WINNING MOVE IS")
            self.history.append("NOT TO PLAY.")
            self.phase = "OVER"
        
        elif ev.button is Button.A and self.phase == "GREETING":
            self.phase = "WAR"
            self.history = ["CONNECTING TO NORAD...", "AUTHORIZING ACCESS...", "DEFCON 5 REACHED."]
            self.timer = time.time()

        elif self.phase == "WAR" and ev.button is Button.A and not self.launched:
            self.launched = True
            self.defcon = 1
            self.history.append("MISSILES LAUNCHED.")
            self.history.append("CALCULATING TRAJECTORIES...")

    def update(self):
        if self.phase == "WAR" and not self.launched:
            if time.time() - self.timer > 3:
                self.timer = time.time()
                self.defcon = max(1, self.defcon - 1)
                self.history.append(f"DEFCON {self.defcon}...")
                if self.defcon == 1:
                    self.history.append("READY FOR TARGET SELECTION.")

    def render(self):
        self.screen.fill((0, 5, 0)) # Deep green CRT feel
        
        # Draw history
        y = 20
        for line in self.history[-15:]:
            txt = self.font_mono.render(line, True, (0, 255, 60))
            self.screen.blit(txt, (20, y))
            y += 25

        # Draw DEFCON
        if self.phase == "WAR":
            color = (255, 0, 0) if self.defcon == 1 else (255, 255, 0)
            defcon_txt = self.font_big.render(f"DEFCON {self.defcon}", True, color)
            self.screen.blit(defcon_txt, (theme.SCREEN_W - 200, 20))
            
            if self.defcon == 1 and not self.launched:
                prompt = self.font_main.render("PRESS [A] TO LAUNCH", True, (255, 0, 0))
                self.screen.blit(prompt, (theme.SCREEN_W // 2 - prompt.get_width() // 2, theme.SCREEN_H - 100))

        if self.phase == "OVER":
            msg = self.font_big.render("GAME OVER", True, (0, 255, 60))
            self.screen.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, theme.SCREEN_H // 2))

def run(screen: pygame.Surface, bus):
    game = Wargames(screen)
    clock = pygame.time.Clock()
    held = set()
    
    while game.running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                return
        
        for bev in bus.drain():
            if bev.pressed:
                held.add(bev.button)
            else:
                held.discard(bev.button)

            # Exit logic: HK+B or SELECT+START or B when game is over
            if bev.pressed:
                if (Button.HK in held and bev.button is Button.B) or \
                   (Button.SELECT in held and Button.START in held):
                    return
                
                if bev.button is Button.B and game.phase == "OVER":
                    return
                
                game.handle(bev)
            
        game.update()
        game.render()
        pygame.display.flip()
        clock.tick(30)
