"""Grid-based launcher.

Displays Sections in a 4x3 grid of icons. Clicking a section opens its
vertical list. Replaces the horizontal Carousel for a more 'appliance-like'
feel.
"""
from __future__ import annotations

import os
from pathlib import Path
import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action, Section, SectionContext


class Launcher:
    def __init__(self, sections: list[Section]) -> None:
        if not sections:
            raise ValueError("Launcher needs at least one section")
        self.sections = sections
        self.index = 0
        self.state = "grid"  # "grid" or "section"
        self._lists = [ScrollList(s.actions) for s in sections]
        
        # Grid layout
        self.cols = 4
        self.rows = 3
        self.icon_size = 64

        # Home background
        self._home_bg = None
        try:
            bg_path = Path(__file__).resolve().parents[2] / "assets" / "home_bg.png"
            if bg_path.exists():
                img = pygame.image.load(str(bg_path)).convert()
                # Cover-fit to screen
                iw, ih = img.get_size()
                scale = max(theme.SCREEN_W / iw, theme.SCREEN_H / ih)
                new_w, new_h = max(1, int(iw * scale)), max(1, int(ih * scale))
                img = pygame.transform.smoothscale(img, (new_w, new_h))
                self._home_bg = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H)).convert()
                self._home_bg.blit(img, ((theme.SCREEN_W - new_w) // 2, (theme.SCREEN_H - new_h) // 2))
                # Add a darkening overlay so icons stay readable
                overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H)).convert()
                overlay.fill((0, 0, 0))
                overlay.set_alpha(160)
                self._home_bg.blit(overlay, (0, 0))
        except Exception as e:
            print(f"[launcher] Failed to load home background: {e}")

    @property
    def current(self) -> Section:
        return self.sections[self.index]

    @property
    def current_list(self) -> ScrollList:
        return self._lists[self.index]

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> Action | None:
        if not ev.pressed:
            return None

        if self.state == "grid":
            if ev.button in (Button.LEFT, Button.LL):
                self.index = (self.index - 1) % len(self.sections)
                return None
            if ev.button in (Button.RIGHT, Button.RR):
                self.index = (self.index + 1) % len(self.sections)
                return None
            if ev.button is Button.UP:
                self.index = (self.index - self.cols) % len(self.sections)
                return None
            if ev.button is Button.DOWN:
                self.index = (self.index + self.cols) % len(self.sections)
                return None
            if ev.button is Button.A:
                self.state = "section"
                self.sections[self.index].on_enter(ctx)
                return None
            return None
        else:
            if ev.button is Button.B and not ev.repeat:
                self.sections[self.index].on_leave(ctx)
                self.state = "grid"
                return None
            # Allow L/R to switch sections even while inside one
            if ev.button in (Button.LL, Button.RR) and not ev.repeat:
                delta = -1 if ev.button is Button.LL else 1
                self.sections[self.index].on_leave(ctx)
                self.index = (self.index + delta) % len(self.sections)
                self.sections[self.index].on_enter(ctx)
                return None
                
            return self.current_list.handle(ev)

    def render(self, surf: pygame.Surface, font: pygame.font.Font, title_font: pygame.font.Font) -> None:
        if self.state == "grid":
            self._render_grid(surf, title_font)
        else:
            self._render_section(surf, font, title_font)

    def _render_grid(self, surf: pygame.Surface, title_font: pygame.font.Font) -> None:
        if self._home_bg:
            surf.blit(self._home_bg, (0, 0))
        else:
            surf.fill(theme.BG)
        
        # Matrix/Cyberpunk scanlines
        for y in range(0, theme.SCREEN_H, 4):
            pygame.draw.line(surf, (15, 18, 25), (0, y), (theme.SCREEN_W, y))

        margin_x = 60
        margin_y = 40
        top_offset = theme.STATUS_BAR_H + 40
        
        available_w = theme.SCREEN_W - 2 * margin_x
        available_h = theme.SCREEN_H - top_offset - margin_y
        
        cell_w = available_w // self.cols
        cell_h = available_h // self.rows
        
        label_font = pygame.font.Font(None, 22)

        for i, section in enumerate(self.sections):
            row = i // self.cols
            col = i % self.cols
            
            x = margin_x + col * cell_w
            y = top_offset + row * cell_h
            
            selected = (i == self.index)
            
            # Selection Highlight
            if selected:
                rect = pygame.Rect(x + 10, y + 5, cell_w - 20, cell_h - 10)
                # Multi-layer glow
                for grow in range(4):
                    alpha = 100 // (grow + 1)
                    s = pygame.Surface((rect.width + grow*2, rect.height + grow*2), pygame.SRCALPHA)
                    pygame.draw.rect(s, (*theme.ACCENT, alpha), (0, 0, s.get_width(), s.get_height()), width=1, border_radius=10)
                    surf.blit(s, (rect.x - grow, rect.y - grow))
                
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=8)
                pygame.draw.rect(surf, theme.ACCENT, rect, width=2, border_radius=8)
            
            # Icon
            icon_draw_size = self.icon_size
            if selected:
                import math
                import time
                pulse = int(4 * math.sin(time.time() * 5))
                icon_draw_size += pulse
                
            icon_x = x + (cell_w - icon_draw_size) // 2
            icon_y = y + 15
            
            if section.icon_img:
                scaled = pygame.transform.smoothscale(section.icon_img, (icon_draw_size, icon_draw_size))
                surf.blit(scaled, (icon_x, icon_y))
            else:
                # Fallback text icon
                char = section.icon.strip("[]") if section.icon else "?"
                txt = title_font.render(char, True, theme.ACCENT if selected else theme.FG_DIM)
                surf.blit(txt, (x + (cell_w - txt.get_width()) // 2, icon_y + 10))
                
            # Label
            color = theme.ACCENT if selected else theme.FG_DIM
            label = label_font.render(section.title.upper(), True, color)
            surf.blit(label, (x + (cell_w - label.get_width()) // 2, y + cell_h - 25))

    def _render_section(self, surf: pygame.Surface, font: pygame.font.Font, title_font: pygame.font.Font) -> None:
        rect = pygame.Rect(0, theme.STATUS_BAR_H, theme.SCREEN_W, theme.SCREEN_H - theme.STATUS_BAR_H)
        section = self.current
        slist = self.current_list

        # Page background
        if section.background_img is not None:
            # The background_img is already 800x412 (screen - status - tab)
            # We blit it at the top of our content area.
            surf.blit(section.background_img, (0, theme.STATUS_BAR_H + 44))
        else:
            pygame.draw.rect(surf, theme.BG, rect)

        # Header bar for the section
        head_h = 44
        head_rect = pygame.Rect(0, theme.STATUS_BAR_H, theme.SCREEN_W, head_h)
        pygame.draw.rect(surf, theme.BG_ALT, head_rect)
        pygame.draw.line(surf, theme.DIVIDER, (0, head_rect.bottom - 1), (theme.SCREEN_W, head_rect.bottom - 1))
        
        # Title + Icon
        tx = theme.PADDING
        if section.icon_img:
            # Use small version for header
            small_icon = pygame.transform.smoothscale(section.icon_img, (24, 24))
            surf.blit(small_icon, (tx, head_rect.y + (head_h - 24) // 2))
            tx += 32
            
        title = title_font.render(section.title, True, theme.ACCENT)
        surf.blit(title, (tx, head_rect.y + (head_h - title.get_height()) // 2))
        
        # Navigation hints
        small = pygame.font.Font(None, theme.FS_SMALL)
        hint = small.render("B BACK TO GRID · L/R SWITCH", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - theme.PADDING, head_rect.y + (head_h - hint.get_height()) // 2))

        # Content list
        list_rect = pygame.Rect(
            theme.PADDING,
            head_rect.bottom + 10,
            theme.SCREEN_W - 2 * theme.PADDING,
            theme.SCREEN_H - head_rect.bottom - 20
        )
        slist.render(surf, list_rect, font)
