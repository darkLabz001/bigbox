from __future__ import annotations
import time
import random
import pygame
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigbox.app import App

from bigbox import theme

ASSET_PATH = Path("assets/monster.png")

class Monster:
    """A retro pixelated monster that lives in the status bar."""

    ANIMATIONS = {
        "IDLE": list(range(0, 4)),
        "WALK": list(range(4, 10)),
        "HAPPY": list(range(10, 14)),
        "HURT": list(range(14, 17)),
    }

    def __init__(self):
        self.sprite_sheet = None
        self.frames = []
        self.frame_size = 24
        self.current_state = "IDLE"
        self.frame_index = 0
        self.last_update = time.time()
        self.state_start = time.time()
        self.pos = [theme.SCREEN_W // 2, 2] # Default center of status bar
        self.velocity = [0, 0]
        self.target_x = theme.SCREEN_W // 2
        
        self._load_assets()

    def _load_assets(self):
        try:
            if ASSET_PATH.exists():
                full_sheet = pygame.image.load(str(ASSET_PATH)).convert_alpha()
                
                # --- High-Contrast B&W Processing ---
                # We want a white sprite to match the BigB0X theme.
                # Convert all non-transparent pixels to theme.FG (usually white)
                # or a very light gray.
                processed = pygame.Surface(full_sheet.get_size(), pygame.SRCALPHA)
                for x in range(full_sheet.get_width()):
                    for y in range(full_sheet.get_height()):
                        r, g, b, a = full_sheet.get_at((x, y))
                        if a > 50: # If pixel is not transparent
                            # Set it to white with the original alpha
                            processed.set_at((x, y), (255, 255, 255, a))
                
                # Sheet is 576x24, so 24 frames of 24x24
                for i in range(24):
                    frame = pygame.Surface((self.frame_size, self.frame_size), pygame.SRCALPHA)
                    frame.blit(processed, (0, 0), (i * self.frame_size, 0, self.frame_size, self.frame_size))
                    self.frames.append(frame)
        except Exception as e:
            print(f"[monster] failed to load assets: {e}")

    def set_state(self, state: str):
        if state in self.ANIMATIONS and self.current_state != state:
            self.current_state = state
            self.frame_index = 0
            self.state_start = time.time()

    def update(self, app: App):
        now = time.time()
        dt = now - self.last_update
        self.last_update = now

        # Animation timing
        anim_speed = 0.15
        if self.current_state == "WALK":
            anim_speed = 0.1
        elif self.current_state == "HAPPY":
            anim_speed = 0.08

        if now - self.state_start > anim_speed:
            self.frame_index = (self.frame_index + 1) % len(self.ANIMATIONS[self.current_state])
            self.state_start = now

        # Logic for transitions
        if self.current_state == "HAPPY" and self.frame_index == len(self.ANIMATIONS["HAPPY"]) - 1:
            # Back to idle after one happy cycle
            self.set_state("IDLE")
        
        if self.current_state == "HURT" and self.frame_index == len(self.ANIMATIONS["HURT"]) - 1:
            self.set_state("IDLE")

        # Movement logic
        if self.current_state == "IDLE":
            # Occasionally walk around
            if random.random() < 0.01:
                self.target_x = random.randint(100, theme.SCREEN_W - 100)
                self.set_state("WALK")
        
        if self.current_state == "WALK":
            dx = self.target_x - self.pos[0]
            if abs(dx) < 2:
                self.set_state("IDLE")
            else:
                self.pos[0] += (2 if dx > 0 else -2)

    def render(self, surf: pygame.Surface):
        if not self.frames:
            return

        anim_frames = self.ANIMATIONS.get(self.current_state, self.ANIMATIONS["IDLE"])
        idx = anim_frames[self.frame_index % len(anim_frames)]
        frame = self.frames[idx]
        
        # If walking left, flip frame
        if self.current_state == "WALK" and self.target_x < self.pos[0]:
            frame = pygame.transform.flip(frame, True, False)

        surf.blit(frame, (self.pos[0] - 12, self.pos[1]))
