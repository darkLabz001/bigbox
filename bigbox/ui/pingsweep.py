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
        self.progress = 0.0
        self.status_msg = "READY"
        
        self.input_mode = True # Start by letting user pick range
        self._cursor = 0 # for range selection or scrolling results
        
        self._stop_scan = False
        self._scan_thread: threading.Thread | None = None
        self._scroll_y = 0

    def _start_scan(self):
        self.hosts.clear()
        self.scanning = True
        self.input_mode = False
        self._stop_scan = False
        self.status_msg = "INITIALIZING NMAP..."
        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()

    def _scan_worker(self):
        """Runs nmap and parses output in real-time."""
        try:
            # Use -sn for ping sweep, -n to skip DNS (faster), -T4 for speed
            cmd = ["nmap", "-sn", "-n", "-T4", self.target_range]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            current_host = None
            
            if proc.stdout:
                for line in proc.stdout:
                    if self._stop_scan:
                        proc.terminate()
                        break
                    
                    # Parse Nmap scan report for 192.168.1.1
                    if "Nmap scan report for" in line:
                        ip = line.split()[-1]
                        current_host = Host(ip=ip)
                        self.hosts.append(current_host)
                        self.status_msg = f"FOUND: {ip}"
                    
                    # Parse Host is up (0.00032s latency).
                    elif "Host is up" in line and current_host:
                        latency_match = re.search(r'\((.*?) latency\)', line)
                        if latency_match:
                            current_host.latency = latency_match.group(1)
                    
                    # Parse Nmap done: 256 IP addresses (1 host up) scanned in 2.34 seconds
                    elif "Nmap done" in line:
                        self.status_msg = "SCAN COMPLETE"
                        
            proc.wait()
        except Exception as e:
            self.status_msg = f"ERROR: {str(e)[:20]}"
        
        self.scanning = False

    def handle(self, ev: ButtonEvent) -> None:
        if not ev.pressed: return
        
        if ev.button is Button.B:
            if self.scanning:
                self._stop_scan = True
            elif not self.input_mode:
                self.input_mode = True
                self.hosts.clear()
            else:
                self.dismissed = True
        
        elif self.input_mode:
            if ev.button is Button.UP:
                # Logic to change preset ranges
                presets = ["192.168.1.0/24", "172.20.10.0/28", "10.0.0.0/24", "8.8.8.0/24"]
                idx = (presets.index(self.target_range) - 1) % len(presets)
                self.target_range = presets[idx]
            elif ev.button is Button.DOWN:
                presets = ["192.168.1.0/24", "172.20.10.0/28", "10.0.0.0/24", "8.8.8.0/24"]
                idx = (presets.index(self.target_range) + 1) % len(presets)
                self.target_range = presets[idx]
            elif ev.button is Button.A:
                self._start_scan()
        
        else: # Results mode
            if ev.button is Button.UP:
                self._scroll_y = max(0, self._scroll_y - 30)
            elif ev.button is Button.DOWN:
                self._scroll_y += 30

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        f_title = pygame.font.Font(None, 32)
        title = f_title.render("RECON :: PING_SWEEP", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        # Status Bar
        pygame.draw.rect(surf, (10, 10, 20), (0, theme.SCREEN_H - 30, theme.SCREEN_W, 30))
        f_small = pygame.font.Font(None, 22)
        status = f_small.render(f"RANGE: {self.target_range} | {self.status_msg}", True, theme.FG)
        surf.blit(status, (theme.PADDING, theme.SCREEN_H - 25))

        if self.input_mode:
            self._render_input(surf, head_h)
        else:
            self._render_results(surf, head_h)

    def _render_input(self, surf: pygame.Surface, offset_y: int):
        f_big = pygame.font.Font(None, 36)
        f_med = pygame.font.Font(None, 28)
        
        msg = f_big.render("SELECT TARGET RANGE", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, offset_y + 60))
        
        # Range Box
        box_w, box_h = 400, 60
        box_x = theme.SCREEN_W//2 - box_w//2
        box_y = offset_y + 120
        pygame.draw.rect(surf, theme.BG_ALT, (box_x, box_y, box_w, box_h), border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT, (box_x, box_y, box_w, box_h), 2, border_radius=10)
        
        range_txt = f_big.render(self.target_range, True, theme.ACCENT)
        surf.blit(range_txt, (theme.SCREEN_W//2 - range_txt.get_width()//2, box_y + 15))
        
        hint = f_med.render("UP/DOWN: Change  A: Start Scan  B: Back", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W//2 - hint.get_width()//2, box_y + 100))

    def _render_results(self, surf: pygame.Surface, offset_y: int):
        # Results area
        res_rect = pygame.Rect(theme.PADDING, offset_y + 10, theme.SCREEN_W - 2*theme.PADDING, theme.SCREEN_H - offset_y - 50)
        pygame.draw.rect(surf, (5, 5, 10), res_rect)
        
        # Clip surface for scrolling
        clip_surf = pygame.Surface((res_rect.width, res_rect.height))
        clip_surf.fill((5, 5, 10))
        
        f_body = pygame.font.Font(None, 26)
        
        for i, host in enumerate(self.hosts):
            y = 10 + i * 35 - self._scroll_y
            if y < -30: continue
            if y > res_rect.height: break
            
            # Row BG
            if i % 2 == 0:
                pygame.draw.rect(clip_surf, (15, 15, 25), (0, y-5, res_rect.width, 30))
            
            # Host Info
            ip_txt = f_body.render(f"[{i+1:02}] {host.ip}", True, theme.FG)
            lat_txt = f_body.render(host.latency, True, theme.ACCENT)
            
            clip_surf.blit(ip_txt, (10, y))
            clip_surf.blit(lat_txt, (res_rect.width - 100, y))
            
            # UP indicator
            pygame.draw.circle(clip_surf, (0, 255, 0), (res_rect.width - 120, y + 10), 5)

        surf.blit(clip_surf, res_rect.topleft)
        
        if self.scanning:
            # Scanning Animation
            scan_y = offset_y + 10 + (int(time.time() * 100) % res_rect.height)
            pygame.draw.line(surf, (0, 255, 0, 100), (res_rect.left, scan_y), (res_rect.right, scan_y), 2)
