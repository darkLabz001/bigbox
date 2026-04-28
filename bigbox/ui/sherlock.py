"""Sherlock UI — Pretty interface for the username search tool."""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygame
from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.runner import run_streaming

if TYPE_CHECKING:
    from bigbox.app import App


@dataclass
class FoundSite:
    site: str
    url: str


class SherlockView:
    def __init__(self, username: str) -> None:
        self.username = username
        self.dismissed = False
        self.found_sites: list[FoundSite] = []
        self.status_msg = f"Searching for '{username}'..."
        self.is_running = True
        self.error_msg = ""
        self.scroll_y = 0
        
        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)
        self.small_font = pygame.font.Font(None, 18)
        
        self._proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run_sherlock, daemon=True)
        self._thread.start()

    def _run_sherlock(self):
        # Wrapped with `stdbuf -oL` because sherlock block-buffers stdout when stdin isn't a TTY.
        cmd = ["stdbuf", "-oL", "sherlock", "--print-found", "--timeout", "5", self.username]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,
            )
            
            # Pattern for: [+] SiteName: URL
            pattern = re.compile(r"\[\+\]\s+([^:]+):\s+(.*)")
            
            for line in self._proc.stdout:
                line = line.strip()
                if not line: continue
                
                match = pattern.search(line)
                if match:
                    site = match.group(1).strip()
                    url = match.group(2).strip()
                    self.found_sites.append(FoundSite(site, url))
                    # Auto-scroll to bottom as new items come in
                    max_visible = (theme.SCREEN_H - 120 - 40) // 30
                    if len(self.found_sites) > max_visible:
                        self.scroll_y = len(self.found_sites) - max_visible
                elif "Searching username" in line:
                    self.status_msg = f"Scanning {self.username}..."
                
            self._proc.wait()
            self.is_running = False
            self.status_msg = f"Finished. Found {len(self.found_sites)} matches."
            
        except Exception as e:
            self.error_msg = f"Error: {e}"
            self.is_running = False

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            self._shutdown()
            self.dismissed = True
        elif ev.button is Button.UP:
            self.scroll_y = max(0, self.scroll_y - 1)
        elif ev.button is Button.DOWN:
            max_visible = (theme.SCREEN_H - 120 - 40) // 30
            self.scroll_y = min(max(0, len(self.found_sites) - max_visible), self.scroll_y + 1)

    def _shutdown(self):
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except: pass

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 60
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        title = self.title_font.render(f"SHERLOCK :: {self.username.upper()}", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        # Status Bar
        status_y = head_h
        status_h = 30
        pygame.draw.rect(surf, (20, 25, 30), (0, status_y, theme.SCREEN_W, status_h))
        s_surf = self.small_font.render(self.status_msg, True, theme.FG)
        surf.blit(s_surf, (theme.PADDING, status_y + (status_h - s_surf.get_height()) // 2))
        
        if self.is_running:
            # Scanning animation (moving green line)
            scan_w = 100
            scan_x = (int(time.time() * 300) % (theme.SCREEN_W + scan_w)) - scan_w
            scan_rect = pygame.Rect(scan_x, status_y + status_h - 2, scan_w, 2)
            pygame.draw.rect(surf, theme.ACCENT, scan_rect)

        # Main Area
        list_rect = pygame.Rect(theme.PADDING, status_y + status_h + 10, 
                                theme.SCREEN_W - 2*theme.PADDING, 
                                theme.SCREEN_H - (status_y + status_h + 10) - 40)
        
        pygame.draw.rect(surf, (5, 5, 10), list_rect)
        pygame.draw.rect(surf, theme.DIVIDER, list_rect, 1)

        # Render found sites
        row_h = 30
        max_visible = list_rect.height // row_h
        
        for i in range(max_visible):
            idx = self.scroll_y + i
            if idx >= len(self.found_sites): break
            
            site = self.found_sites[idx]
            y = list_rect.y + i * row_h
            
            # Icon placeholder
            pygame.draw.circle(surf, theme.ACCENT_DIM, (list_rect.x + 15, y + row_h//2), 6)
            
            name_surf = self.body_font.render(site.site, True, theme.ACCENT)
            surf.blit(name_surf, (list_rect.x + 35, y + 4))
            
            url_surf = self.small_font.render(site.url, True, theme.FG_DIM)
            surf.blit(url_surf, (list_rect.x + 180, y + 8))
            
            if i < max_visible - 1:
                pygame.draw.line(surf, (20, 20, 30), (list_rect.x, y + row_h), (list_rect.right, y + row_h))

        if not self.found_sites and not self.is_running and not self.error_msg:
            msg = self.body_font.render("No matches found.", True, theme.FG_DIM)
            surf.blit(msg, (list_rect.centerx - msg.get_width()//2, list_rect.centery))
        elif self.error_msg:
            msg = self.body_font.render(self.error_msg, True, theme.ERR)
            surf.blit(msg, (list_rect.centerx - msg.get_width()//2, list_rect.centery))

        # Footer
        hint = self.small_font.render("UP/DOWN: Scroll  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.PADDING, theme.SCREEN_H - 30))
