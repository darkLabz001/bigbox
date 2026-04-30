"""The Oregon Trail — A retro survival simulation.
Designed for bigbox (800x480) with ButtonEvent support.
"""
from __future__ import annotations
import pygame
import time
import random
from bigbox import theme
from bigbox.events import Button, ButtonEvent

class OregonTrail:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        self.running = True
        self.font_main = pygame.font.Font(None, 24)
        self.font_big = pygame.font.Font(None, 48)
        self.font_mono = pygame.font.SysFont("monospace", 18)
        
        self.miles = 2000
        self.food = 500
        self.health = 100
        self.oxen = 4
        self.day = 1
        
        self.history = [
            "THE OREGON TRAIL",
            "----------------",
            "YOU HAVE SET OUT FROM INDEPENDENCE, MO.",
            "WITH 2000 MILES TO REACH OREGON CITY.",
            "",
            "[A] CONTINUE TRAVEL",
            "[X] HUNT FOR FOOD",
            "[Y] REST",
            "[B] ABANDON TRAIL"
        ]
        self.phase = "TRAVEL"
        self.last_event = ""

    def handle(self, ev: ButtonEvent):
        if not ev.pressed: return
        
        if ev.button is Button.B:
            self.history.append("> ABANDONED")
            self.phase = "OVER"
            return

        if self.phase == "TRAVEL":
            if ev.button is Button.A:
                dist = random.randint(30, 70)
                self.miles -= dist
                self.food -= random.randint(10, 20)
                self.day += 1
                self.history.append(f"DAY {self.day}: TRAVELED {dist} MILES.")
                self._random_event()
            elif ev.button is Button.X:
                gain = random.randint(20, 100)
                self.food += gain
                self.history.append(f"HUNT SUCCESS: +{gain} LBS FOOD.")
                self.day += 1
            elif ev.button is Button.Y:
                self.health = min(100, self.health + 20)
                self.food -= 5
                self.history.append("RESTED. HEALTH RECOVERED.")
                self.day += 1

        if self.miles <= 0:
            self.history.append("YOU HAVE REACHED OREGON!")
            self.phase = "OVER"
        elif self.health <= 0 or self.food <= 0:
            self.history.append("YOU HAVE DIED.")
            self.phase = "OVER"

    def _random_event(self):
        r = random.random()
        if r < 0.1:
            self.history.append("EVENT: DYSENTERY!")
            self.health -= 30
        elif r < 0.2:
            self.history.append("EVENT: AN OX DIED.")
            self.oxen -= 1
        elif r < 0.3:
            self.history.append("EVENT: ROUGH TERRAIN. SLOWED DOWN.")
            self.miles += 10

    def render(self):
        self.screen.fill(theme.BG)
        
        # Stats Bar
        pygame.draw.rect(self.screen, theme.BG_ALT, (0, 0, theme.SCREEN_W, 40))
        stats = f"MILES: {max(0, self.miles)} | FOOD: {self.food} | HEALTH: {self.health}% | OXEN: {self.oxen}"
        st_surf = self.font_main.render(stats, True, theme.ACCENT)
        self.screen.blit(st_surf, (20, 10))
        
        # History
        y = 60
        for line in self.history[-12:]:
            txt = self.font_mono.render(line, True, theme.FG)
            self.screen.blit(txt, (20, y))
            y += 28

        if self.phase == "OVER":
            msg = self.font_big.render("GAME OVER", True, theme.ERR)
            self.screen.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, theme.SCREEN_H // 2))

def run(screen: pygame.Surface, bus):
    game = OregonTrail(screen)
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
            
        game.render()
        pygame.display.flip()
        clock.tick(30)
