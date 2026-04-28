"""Glitch View — Interactive Pwnagotchi-style UI for DaRkb0x."""
from __future__ import annotations

import time
import math
import random
from pathlib import Path

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.glitch import GlitchEngine, Host

class GlitchView:
    def __init__(self, engine: GlitchEngine):
        self.engine = engine
        self.dismissed = False
        
        # Load Sprite Sheet (1448 x 1086 -> 4x3 grid)
        sprite_path = Path(__file__).resolve().parents[2] / "assets" / "glitch" / "sprite_sheet.png"
        self.frames = []
        if sprite_path.exists():
            sheet = pygame.image.load(str(sprite_path)).convert_alpha()
            fw, fh = 362, 362
            for y in range(3):
                for x in range(4):
                    self.frames.append(sheet.subsurface(pygame.Rect(x*fw, y*fh, fw, fh)))

        self.anim_frame = 0
        self.last_anim = time.time()
        self.selected_host = 0
        self.glitch_tick = 0

    def _draw_glitch_sprite(self, screen: pygame.Surface, pos: tuple[int, int]):
        if not self.frames: return
        
        # 1. Update Animation
        now = time.time()
        rate = 0.08 if self.engine.current_activity != "IDLE" else 0.12
        if now - self.last_anim > rate:
            self.anim_frame = (self.anim_frame + 1) % len(self.frames)
            self.last_anim = now

        frame = self.frames[self.anim_frame].copy()
        
        # 2. Procedural "Glitch" Distortion (Pwnagotchi-style reactivity)
        if random.random() < 0.05 or self.engine.current_activity == "ATTACKING":
            for _ in range(random.randint(1, 4)):
                y = random.randint(0, 361)
                h = random.randint(2, 10)
                shift = random.randint(-15, 15)
                if y + h < 362:
                    slice_rect = pygame.Rect(0, y, 362, h)
                    part = frame.subsurface(slice_rect).copy()
                    frame.blit(part, (shift, y))

        # 3. Dynamic Scaling / Bobbing
        bob = math.sin(now * 4) * 10
        scale = 1.0 + math.sin(now * 2) * 0.02
        if scale != 1.0:
            frame = pygame.transform.smoothscale(frame, (int(362*scale), int(362*scale)))
        
        rect = frame.get_rect(center=(pos[0] + 181, pos[1] + 181 + bob))
        screen.blit(frame, rect)

    def render(self, screen: pygame.Surface):
        # Pure High Contrast
        CLR_BG = (0, 0, 0)
        CLR_FG = (255, 255, 255)
        CLR_DIM = (120, 120, 120)

        screen.fill(CLR_BG)
        
        # A. Background HUD Elements
        pygame.draw.rect(screen, CLR_FG, (0, 0, 800, 30)) # Top Bar
        f_stat = pygame.font.Font(None, 24)
        screen.blit(f_stat.render(f"GLITCH_OS v1.0 // {self.engine.current_activity}", True, CLR_BG), (10, 5))
        screen.blit(f_stat.render(datetime.now().strftime("%H:%M:%S"), True, CLR_BG), (710, 5))

        # B. The Companion
        self._draw_glitch_sprite(screen, (30, 60))

        # C. The "Thought" Bubble (Pwnagotchi style)
        f_thought = pygame.font.Font(None, 32)
        thought_txt = f"> {self.engine.thought}"
        # Typewriter effect or just static? Let's do static for now
        tw = f_thought.render(thought_txt, True, CLR_FG)
        screen.blit(tw, (420, 80))
        
        # D. Status HUD
        pygame.draw.line(screen, CLR_DIM, (420, 120), (780, 120), 1)
        screen.blit(f_stat.render(self.engine.status_text, True, CL_DIM := (180, 180, 180)), (420, 130))

        # E. Target Grid
        list_rect = pygame.Rect(420, 170, 360, 220)
        pygame.draw.rect(screen, (20, 20, 20), list_rect)
        pygame.draw.rect(screen, CLR_DIM, list_rect, 1)
        
        with self.engine.lock:
            hosts = list(self.engine.hosts.values())
        
        if not hosts:
            screen.blit(f_stat.render("SILENCE IN THE WIRE...", True, CLR_DIM), (440, 260))
        else:
            for i, host in enumerate(hosts[-7:]): # Last 7
                y = 180 + (i * 30)
                color = CLR_FG
                if i == (self.selected_host % 7):
                    pygame.draw.rect(screen, CLR_FG, (425, y-2, 350, 26))
                    color = CLR_BG
                
                txt = f"{host.ip.ljust(15)} | {host.status.upper()}"
                screen.blit(f_stat.render(txt, True, color), (430, y))

        # F. Bottom "Data" Bar
        pygame.draw.rect(screen, CLR_DIM, (0, 450, 800, 30))
        hint = "[UP/DOWN] NAVIGATE  [A] OVERRIDE  [B] EXIT  [PORT 8888]"
        screen.blit(f_stat.render(hint, True, CLR_BG), (10, 455))

    def handle(self, bev: ButtonEvent, app: App):
        if bev.pressed:
            if bev.button == Button.B: self.dismissed = True
            elif bev.button == Button.DOWN: self.selected_host += 1
            elif bev.button == Button.UP: self.selected_host -= 1
            elif bev.button == Button.A:
                with self.engine.lock:
                    hosts = list(self.engine.hosts.values())
                    if hosts:
                        idx = self.selected_host % len(hosts)
                        hosts[idx].status = "attacking"
                        self.engine.thought = f"Manual override on {hosts[idx].ip}."
