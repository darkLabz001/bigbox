"""Ping Sweep Tool — high-performance host discovery with a dedicated UI."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent


@dataclass
class Host:
    ip: str
    status: str = "UP"
    vendor: str = "Unknown"
    latency: str = "0ms"


class PingSweepView:
    """Dedicated UI for ping sweeping network ranges."""

    def __init__(self) -> None:
        self.target_range = "192.168.1.0/24"
        self.hosts: list[Host] = []
        self.scanning = False
        self.dismissed = False
        self.status_msg = "READY"
        
        self.input_mode = True 
        self._stop_scan = False
        self._proc: subprocess.Popen | None = None
        self._scan_thread: threading.Thread | None = None
        self._scroll_y = 0

    def _start_scan(self):
        self.hosts.clear()
        self.scanning = True
        self.input_mode = False
        self._stop_scan = False
        self._scroll_y = 0
        self.status_msg = "SCANNING..."
        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()

    def _scan_worker(self):
        """Runs nmap and parses output in real-time."""
        try:
            cmd = ["nmap", "-sn", "-n", "-T4", self.target_range]
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            current_host = None
            
            if self._proc.stdout:
                for line in self._proc.stdout:
                    if self._stop_scan:
                        break
                    
                    if "Nmap scan report for" in line:
                        ip = line.split()[-1]
                        current_host = Host(ip=ip)
                        self.hosts.append(current_host)
                        # Keep view scrolled to bottom if we are at bottom
                        max_scroll = max(0, len(self.hosts) * 35 - 300)
                        if self._scroll_y >= max_scroll - 70:
                            self._scroll_y = max_scroll

                    elif "Host is up" in line and current_host:
                        latency_match = re.search(r'\((.*?) latency\)', line)
                        if latency_match:
                            current_host.latency = latency_match.group(1)
                    
                    elif "Nmap done" in line:
                        self.status_msg = "SCAN COMPLETE"
                        
            if self._proc:
                self._proc.wait(timeout=1.0)
        except Exception as e:
            if not self._stop_scan:
                self.status_msg = f"ERROR: {str(e)[:20]}"
        finally:
            self.scanning = False
            self._proc = None

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed: return
        
        if ev.button is Button.B:
            if self.scanning:
                self._stop_scan = True
                if self._proc:
                    try:
                        self._proc.kill()
                    except Exception: pass
                self.status_msg = "SCAN CANCELED"
                self.scanning = False
            elif not self.input_mode:
                self.input_mode = True
                self.hosts.clear()
                self.status_msg = "READY"
            else:
                self.dismissed = True
        
        elif self.input_mode:
            presets = ["192.168.1.0/24", "172.20.10.0/28", "10.0.0.0/24", "8.8.8.8", "1.1.1.1"]
            try:
                idx = presets.index(self.target_range)
            except ValueError:
                idx = 0
                
            if ev.button is Button.UP:
                self.target_range = presets[(idx - 1) % len(presets)]
            elif ev.button is Button.DOWN:
                self.target_range = presets[(idx + 1) % len(presets)]
            elif ev.button is Button.A:
                self._start_scan()
        
        else: # Results mode
            if ev.button is Button.UP:
                self._scroll_y = max(0, self._scroll_y - 40)
            elif ev.button is Button.DOWN:
                max_scroll = max(0, len(self.hosts) * 35 - 300)
                self._scroll_y = min(max_scroll, self._scroll_y + 40)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        f_title = pygame.font.Font(None, 32)
        title = f_title.render("RECON :: PING_SWEEP", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        # Stats in header
        f_small = pygame.font.Font(None, 22)
        host_count = f_small.render(f"HOSTS: {len(self.hosts)}", True, theme.FG)
        surf.blit(host_count, (theme.SCREEN_W - 120, (head_h - host_count.get_height()) // 2))

        # Status Bar
        pygame.draw.rect(surf, (10, 10, 20), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - 35), (theme.SCREEN_W, theme.SCREEN_H - 35))
        
        status = f_small.render(f"RANGE: {self.target_range} | {self.status_msg}", True, theme.ACCENT)
        surf.blit(status, (theme.PADDING, theme.SCREEN_H - 28))

        if self.input_mode:
            self._render_input(surf, head_h)
        else:
            self._render_results(surf, head_h)

    def _render_input(self, surf: pygame.Surface, offset_y: int):
        f_big = pygame.font.Font(None, 38)
        f_med = pygame.font.Font(None, 28)
        
        msg = f_big.render("SELECT TARGET RANGE", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, offset_y + 70))
        
        # Range Box
        box_w, box_h = 450, 70
        box_x = theme.SCREEN_W//2 - box_w//2
        box_y = offset_y + 130
        pygame.draw.rect(surf, (20, 30, 40), (box_x, box_y, box_w, box_h), border_radius=5)
        pygame.draw.rect(surf, theme.ACCENT, (box_x, box_y, box_w, box_h), 2, border_radius=5)
        
        range_txt = f_big.render(self.target_range, True, theme.ACCENT)
        surf.blit(range_txt, (theme.SCREEN_W//2 - range_txt.get_width()//2, box_y + 20))
        
        hint = f_med.render("UP/DOWN: Cycle Presets  A: START  B: BACK", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W//2 - hint.get_width()//2, theme.SCREEN_H - 80))

    def _render_results(self, surf: pygame.Surface, offset_y: int):
        res_rect = pygame.Rect(theme.PADDING, offset_y + 10, theme.SCREEN_W - 2*theme.PADDING, theme.SCREEN_H - offset_y - 50)
        
        # Draw Background for results
        pygame.draw.rect(surf, (5, 5, 10), res_rect)
        pygame.draw.rect(surf, theme.DIVIDER, res_rect, 1)
        
        # List area with clipping
        list_rect = res_rect.inflate(-20, -20)
        
        f_body = pygame.font.Font(None, 28)
        
        for i, host in enumerate(self.hosts):
            y = list_rect.y + i * 35 - self._scroll_y
            if y < list_rect.y - 30: continue
            if y > list_rect.bottom: break
            
            # Row highlight
            if i % 2 == 0:
                pygame.draw.rect(surf, (15, 20, 30), (list_rect.x, y-5, list_rect.width, 30))
            
            # Host Details
            idx_txt = f_body.render(f"{i+1:02}", True, theme.FG_DIM)
            ip_txt = f_body.render(host.ip, True, theme.FG)
            lat_txt = f_body.render(f"{host.latency}", True, theme.ACCENT)
            
            surf.blit(idx_txt, (list_rect.x + 5, y))
            surf.blit(ip_txt, (list_rect.x + 50, y))
            surf.blit(lat_txt, (list_rect.right - 100, y))
            
            # Status Indicator
            pygame.draw.circle(surf, (0, 255, 100), (list_rect.right - 120, y + 12), 6)

        # Scrolling Indicators
        if self._scroll_y > 0:
            pygame.draw.polygon(surf, theme.ACCENT, [(res_rect.right-10, res_rect.y+10), (res_rect.right-20, res_rect.y+20), (res_rect.right, res_rect.y+20)])
        
        # Controls Hint overlay
        f_hint = pygame.font.Font(None, 20)
        if self.scanning:
            hint = f_hint.render("PRESS B TO STOP SCAN", True, theme.ERR)
        else:
            hint = f_hint.render("UP/DOWN: Scroll  B: New Scan / Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - 20, theme.SCREEN_H - 28))

        if self.scanning:
            # High-tech scanline sweep
            scan_y = res_rect.y + (int(time.time() * 150) % res_rect.height)
            pygame.draw.line(surf, (0, 255, 0, 150), (res_rect.left, scan_y), (res_rect.right, scan_y), 3)
