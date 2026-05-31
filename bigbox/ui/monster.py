from __future__ import annotations
import time
import random
import pygame
import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bigbox.app import App

from bigbox import theme

class Monster:
    """Pwnagotchi-style companion for the Operator.
    Replaces the old demon with the classic AI face.
    """

    MOODS = ("intense", "excited", "calm", "alert", "sad", "happy", "look", "sleep")
    
    def __init__(self):
        self.sprites: dict[str, pygame.Surface] = {}
        self.display_size = 120 # Pwnagotchi face size
        self.current_mood = "calm"
        self.last_update = time.time()
        self.mood_start = time.time()
        self.mood_duration = 0.0
        
        # Position in the sidebar (centered)
        self.pos = [85, 260] 
        
        self._loaded = False
        self._blink_state = False
        self._last_blink = time.time()

    def _load_assets(self):
        if self._loaded:
            return
            
        try:
            if not pygame.display.get_init() or pygame.display.get_surface() is None:
                return

            ROOT = Path(__file__).resolve().parents[2]
            SPR_DIR = ROOT / "assets" / "sprites" / "pwn"
            
            self.sprites = {}
            for m in self.MOODS:
                p = SPR_DIR / f"{m}.png"
                if p.exists():
                    img = pygame.image.load(str(p)).convert_alpha()
                    # Scale to fit the sidebar well
                    h = self.display_size
                    w = int(img.get_width() * (h / img.get_height()))
                    scaled = pygame.transform.smoothscale(img, (w, h))
                    self.sprites[m] = scaled
            
            if self.sprites:
                self._loaded = True
                print(f"[monster] Pwnagotchi face loaded ({len(self.sprites)} moods)")
            else:
                print(f"[monster] Error: Pwnagotchi sprites missing at {SPR_DIR}")
        except Exception as e:
            print(f"[monster] Load failure: {e}")

    def set_state(self, mood: str, duration: float = 0.0):
        """Set the current mood. If duration > 0, it will revert to 'calm' after."""
        if mood in self.MOODS:
            self.current_mood = mood
            self.mood_start = time.time()
            self.mood_duration = duration

    def update(self, app: App):
        now = time.time()
        
        # Revert temporary moods
        if self.mood_duration > 0 and now - self.mood_start > self.mood_duration:
            self.current_mood = "calm"
            self.mood_duration = 0.0

        # Random look around / blink
        if self.current_mood == "calm":
            if now - self._last_blink > 3.0 + random.random() * 5.0:
                self._blink_state = True
                if now - self._last_blink > 3.2: # Short blink
                    self._blink_state = False
                    self._last_blink = now
            
            if random.random() < 0.005:
                self.set_state("look", 2.0)

    def render(self, surf: pygame.Surface):
        if not self._loaded:
            self._load_assets()

        if not self.sprites:
            return

        mood = self.current_mood
        # Blinking logic: "sleep" is a horizontal line (closed eyes)
        if self._blink_state and mood in ("calm", "happy", "alert"):
            mood = "sleep"

        frame = self.sprites.get(mood, self.sprites.get("calm"))
        if frame:
            # Draw centered on pos
            rect = frame.get_rect(center=(self.pos[0], self.pos[1]))
            surf.blit(frame, rect)
