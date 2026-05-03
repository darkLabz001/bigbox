"""Loot Gallery — Unified visualizer for handshakes, credentials, and scans."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App


PHASE_CATEGORIES = "categories"
PHASE_LIST = "list"
PHASE_VIEW = "view"


class LootGalleryView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_CATEGORIES
        self.category_cursor = 0
        self.categories = [
            {"id": "handshakes", "label": "Handshake Gallery", "path": Path("loot/handshakes"), "icon": "H"},
            {"id": "captive", "label": "Captured Credentials", "path": Path("loot/captive"), "icon": "C"},
            {"id": "scans", "label": "Scan History", "path": Path("loot/scans"), "icon": "S"},
        ]

        self.list = ScrollList([])
        self.current_category = None
        
        self.view_title = ""
        self.view_text = ""
        self.view_scroll = 0

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.hint_font = pygame.font.Font(None, theme.FS_SMALL)
        self.mono_font = pygame.font.Font(None, 20)

    def _refresh_list(self, category):
        path = category["path"]
        actions = []
        if not path.exists():
            actions.append(Action(f"[ No {category['id']} found ]", None))
            self.list = ScrollList(actions)
            return

        files = sorted(path.iterdir(), key=lambda f: f.stat().st_mtime, reverse=True)
        
        for f in files:
            if f.is_file() and not f.name.startswith("."):
                label = f.name
                desc = f"{f.stat().st_size / 1024:.1f} KB"
                
                # Custom description for certain types
                if category["id"] == "handshakes" and f.suffix == ".pcapng":
                    # Try to extract SSID from filename
                    # Format usually: SSID_BSSID_TS-01.pcapng
                    match = re.match(r"^(.*)_[0-9A-Fa-f]{12}_", f.name)
                    if match:
                        desc = f"Handshake :: {match.group(1)}"
                
                def make_handler(p=f):
                    return lambda ctx: self._open_file(p, category["id"])
                
                actions.append(Action(label, make_handler(), desc))
        
        if not actions:
            actions.append(Action(f"[ No {category['id']} found ]", None))
        
        self.list = ScrollList(actions)

    def _open_file(self, path: Path, cat_id: str):
        self.view_title = path.name
        self.view_scroll = 0
        
        try:
            if cat_id == "handshakes" and path.suffix in (".pcap", ".pcapng"):
                # Use hcxpcapngtool if available to show info? 
                # For now just show basic info
                self.view_text = f"Binary Packet Capture: {path.name}\n\n"
                self.view_text += f"Path: {path}\n"
                self.view_text += f"Size: {path.stat().st_size} bytes\n\n"
                self.view_text += "Use 'hcxpcapngtool' or Wireshark off-device to inspect."
            else:
                with path.open("r", errors="replace") as f:
                    self.view_text = f.read()
        except Exception as e:
            self.view_text = f"Error reading file: {e}"
            
        self.phase = PHASE_VIEW

    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return

        if self.phase == PHASE_CATEGORIES:
            if ev.button is Button.B:
                self.dismissed = True
            elif ev.button is Button.UP:
                self.category_cursor = (self.category_cursor - 1) % len(self.categories)
            elif ev.button is Button.DOWN:
                self.category_cursor = (self.category_cursor + 1) % len(self.categories)
            elif ev.button is Button.A:
                self.current_category = self.categories[self.category_cursor]
                self._refresh_list(self.current_category)
                self.phase = PHASE_LIST
            return

        if self.phase == PHASE_LIST:
            if ev.button is Button.B:
                self.phase = PHASE_CATEGORIES
                return
            action = self.list.handle(ev)
            if action and action.handler:
                action.handler(ctx)
            return

        if self.phase == PHASE_VIEW:
            if ev.button is Button.B:
                self.phase = PHASE_LIST
            elif ev.button is Button.UP:
                self.view_scroll = max(0, self.view_scroll - 60)
            elif ev.button is Button.DOWN:
                self.view_scroll += 60
            return

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1), (theme.SCREEN_W, head_h - 1), 2)

        title_text = "LOOT GALLERY"
        if self.phase == PHASE_LIST and self.current_category:
            title_text = f"LOOT :: {self.current_category['label'].upper()}"
        elif self.phase == PHASE_VIEW:
            title_text = f"VIEW :: {self.view_title}"

        title = self.title_font.render(title_text, True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        if self.phase == PHASE_CATEGORIES:
            self._render_categories(surf, head_h)
        elif self.phase == PHASE_LIST:
            list_rect = pygame.Rect(theme.PADDING, head_h + theme.PADDING,
                                    theme.SCREEN_W - 2 * theme.PADDING,
                                    theme.SCREEN_H - head_h - 2 * theme.PADDING - 40)
            self.list.render(surf, list_rect, self.body_font)
            hint = self.hint_font.render("UP/DOWN: Navigate  A: Open  B: Back", True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
        elif self.phase == PHASE_VIEW:
            body_rect = pygame.Rect(theme.PADDING, head_h + theme.PADDING,
                                    theme.SCREEN_W - 2 * theme.PADDING,
                                    theme.SCREEN_H - head_h - 2 * theme.PADDING - 40)
            self._render_view(surf, body_rect)
            hint = self.hint_font.render("UP/DOWN: Scroll  B: Back", True, theme.FG_DIM)
            surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

    def _render_categories(self, surf: pygame.Surface, head_h: int):
        box_w, box_h = 600, 80
        start_y = head_h + 40
        
        for i, cat in enumerate(self.categories):
            sel = i == self.category_cursor
            rect = pygame.Rect((theme.SCREEN_W - box_w) // 2, start_y + i * (box_h + 20), box_w, box_h)
            
            bg = theme.SELECTION_BG if sel else theme.BG_ALT
            border = theme.ACCENT if sel else theme.DIVIDER
            
            pygame.draw.rect(surf, bg, rect, border_radius=8)
            pygame.draw.rect(surf, border, rect, 2 if sel else 1, border_radius=8)
            
            # Icon box
            icon_rect = pygame.Rect(rect.x + 10, rect.y + 10, 60, 60)
            pygame.draw.rect(surf, theme.BG, icon_rect, border_radius=4)
            icon_font = pygame.font.Font(None, 40)
            icon_surf = icon_font.render(cat["icon"], True, theme.ACCENT)
            surf.blit(icon_surf, (icon_rect.centerx - icon_surf.get_width() // 2, icon_rect.centery - icon_surf.get_height() // 2))
            
            label_surf = self.body_font.render(cat["label"], True, theme.FG)
            surf.blit(label_surf, (rect.x + 90, rect.y + 15))
            
            path_surf = self.hint_font.render(str(cat["path"]), True, theme.FG_DIM)
            surf.blit(path_surf, (rect.x + 90, rect.y + 45))

        hint = self.hint_font.render("UP/DOWN: Select  A: Enter Category  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))

    def _render_view(self, surf: pygame.Surface, body: pygame.Rect):
        pygame.draw.rect(surf, (5, 5, 10), body)
        pygame.draw.rect(surf, theme.DIVIDER, body, 1)
        
        line_h = 24
        x = body.x + 10
        y = body.y + 10 - self.view_scroll
        
        for line in self.view_text.splitlines():
            if y > body.bottom: break
            if y + line_h >= body.y:
                color = theme.FG
                if "password" in line.lower() or "pin" in line.lower():
                    color = theme.WARN
                ls = self.mono_font.render(line[:100], True, color)
                surf.blit(ls, (x, y))
            y += line_h
