"""Bettercap Dashboard — Real-time network monitoring and MITM.

Launches bettercap and parses its JSON/text output to show a live 
dashboard of the current network.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Dict, List, Optional

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

class BettercapView:
    def __init__(self) -> None:
        self.dismissed = False
        self.proc: Optional[subprocess.Popen] = None
        self.hosts: Dict[str, Dict] = {} # mac -> {ip, vendor, last_seen}
        self.events: List[str] = []
        self.status = "INITIALIZING_ENGINE"
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 22)
        self.f_log = pygame.font.Font(None, 18)
        self.f_tiny = pygame.font.Font(None, 14)
        
        self.is_spoofing = False
        self.is_probing = True
        
        self._start_engine()

    def _start_engine(self):
        # We run bettercap with -no-colors and -no-history for easier parsing
        # We'll use a caplet-like command string
        cmd = [
            "sudo", "bettercap",
            "-iface", "wlan0",
            "-no-colors",
            "-eval", "net.probe on; ticker on; set ticker.commands 'net.show; events.show 5'; set ticker.period 2"
        ]
        
        def _reader():
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                self.status = "ENGINE_RUNNING"
                
                # Simple regex for host parsing (Bettercap net.show table)
                # Format: IP | MAC | Name | Vendor | Sent | Recvd | Last Seen
                host_re = re.compile(r"([\d\.]+)\s+([0-9a-f:]{17})\s+(.*?)\s+(.*?)\s+\d+")
                
                for line in self.proc.stdout:
                    if self.dismissed: break
                    
                    line = line.strip()
                    if not line: continue
                    
                    # Parse hosts
                    m = host_re.search(line)
                    if m:
                        ip, mac, name, vendor = m.groups()
                        self.hosts[mac] = {
                            "ip": ip,
                            "vendor": vendor.strip() or "Unknown",
                            "last_seen": time.time()
                        }
                    elif "[at]" in line or "[sys.log]" in line:
                        # Event logs
                        clean = re.sub(r'\[.*?\]', '', line).strip()
                        if clean:
                            self.events.append(clean)
                            if len(self.events) > 50: self.events.pop(0)
                            
            except Exception as e:
                self.status = f"ENGINE_ERROR: {str(e)[:20]}"
            
        threading.Thread(target=_reader, daemon=True).start()

    def _send_cmd(self, bettercap_cmd: str):
        # Not easily supported with Popen(stdout=PIPE) unless we use stdin=PIPE too
        # For v1, we'll just toggle modules by restarting with different -eval
        pass

    def _stop_engine(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=2)
            self.proc = None

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._stop_engine()
            self.dismissed = True
        elif ev.button is Button.X:
            # Toggle Probing (in a real impl we'd send cmd to stdin)
            self.is_probing = not self.is_probing
            # ctx.toast("PROBE_TOGGLED")

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("NETWORK :: BETTERCAP_DASHBOARD", True, theme.ACCENT), (theme.PADDING, 8))
        
        # Grid layout
        # Left: Host List (IP / Vendor)
        # Right: Event Log
        
        half_w = theme.SCREEN_W // 2
        
        # 1. Hosts
        surf.blit(self.f_main.render("DISCOVERED_HOSTS", True, theme.FG_DIM), (20, head_h + 10))
        pygame.draw.line(surf, theme.DIVIDER, (20, head_h + 30), (half_w - 20, head_h + 30))
        
        y = head_h + 40
        sorted_hosts = sorted(self.hosts.values(), key=lambda x: x["last_seen"], reverse=True)
        for h in sorted_hosts[:12]:
            col = theme.FG
            if time.time() - h["last_seen"] < 5: col = theme.ACCENT
            
            ip_txt = f"{h['ip']:<15}"
            vendor = h['vendor'][:18]
            surf.blit(self.f_main.render(ip_txt, True, col), (25, y))
            surf.blit(self.f_tiny.render(vendor.upper(), True, theme.FG_DIM), (150, y + 4))
            y += 22
            
        # 2. Events
        surf.blit(self.f_main.render("NETWORK_EVENTS", True, theme.FG_DIM), (half_w + 10, head_h + 10))
        pygame.draw.line(surf, theme.DIVIDER, (half_w + 10, head_h + 30), (theme.SCREEN_W - 20, head_h + 30))
        
        y = head_h + 40
        for e in reversed(self.events[-18:]):
            txt = e[:45]
            surf.blit(self.f_log.render(f"> {txt}", True, theme.FG), (half_w + 15, y))
            y += 18

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        status_col = theme.ACCENT if "RUNNING" in self.status else theme.ERR
        surf.blit(self.f_small.render(f"STATUS: {self.status}", True, status_col), (10, theme.SCREEN_H - 26))
        
        hint = "X: TOGGLE PROBE  B: BACK"
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))
