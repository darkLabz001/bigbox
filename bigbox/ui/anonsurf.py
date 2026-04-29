"""Anon Surf — System-wide transparent proxy through Tor.

Bypasses IP bans by routing all TCP traffic through the Tor network.
Uses iptables to redirect outgoing traffic to Tor's TransPort (9040)
and DNS traffic to Tor's DNSPort (5353).
"""
from __future__ import annotations

import subprocess
import threading
import time
from typing import TYPE_CHECKING, Optional

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


class AnonSurfView:
    def __init__(self) -> None:
        self.dismissed = False
        self.status = "CHECKING_STATE"
        self.public_ip = "???"
        self.is_active = False
        self.is_working = False
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 20)
        
        self._refresh_state()

    def _refresh_state(self):
        self.is_working = True
        def _worker():
            try:
                # Check if iptables has redirection to 9040
                res = subprocess.run(["sudo", "iptables", "-t", "nat", "-L"], 
                                   capture_output=True, text=True)
                self.is_active = "9040" in res.stdout
                self.status = "STEALTH_ACTIVE" if self.is_active else "CLEAN_UPLINK"
                
                # Try to get public IP
                try:
                    ip_res = subprocess.run(["curl", "-s", "--max-time", "5", "https://api.ipify.org"], 
                                         capture_output=True, text=True)
                    self.public_ip = ip_res.stdout.strip() or "OFFLINE"
                except:
                    self.public_ip = "OFFLINE"
            except Exception as e:
                self.status = f"ERROR: {str(e)[:20]}"
            self.is_working = False
            
        threading.Thread(target=_worker, daemon=True).start()

    def _toggle(self):
        if self.is_working: return
        self.is_working = True
        
        def _worker():
            try:
                if self.is_active:
                    self.status = "STOPPING_STEALTH..."
                    # Stop: Flush nat table, restore DNS
                    subprocess.run(["sudo", "iptables", "-t", "nat", "-F"])
                    subprocess.run(["sudo", "systemctl", "restart", "systemd-resolved"], stderr=subprocess.DEVNULL)
                else:
                    self.status = "STARTING_STEALTH..."
                    # Start: Ensure Tor is running
                    subprocess.run(["sudo", "systemctl", "start", "tor"])
                    
                    # IPTables rules for transparent proxy
                    rules = [
                        # Allow loopback
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-o", "lo", "-j", "RETURN"],
                        # Allow local networks
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-d", "192.168.0.0/16", "-j", "RETURN"],
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-d", "172.16.0.0/12", "-j", "RETURN"],
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-d", "10.0.0.0/8", "-j", "RETURN"],
                        # Exception for GitHub (allows OTA updates to function)
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-d", "140.82.112.0/20", "-j", "RETURN"],
                        # Tor's own traffic
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-m", "owner", "--uid-owner", "debian-tor", "-j", "RETURN"],
                        # Redirect DNS to Tor's DNSPort
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-p", "udp", "--dport", "53", "-j", "REDIRECT", "--to-ports", "5353"],
                        # Redirect all other TCP to Tor's TransPort
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-p", "tcp", "--syn", "-j", "REDIRECT", "--to-ports", "9040"],
                        # Block other UDP to prevent leaks
                        ["iptables", "-t", "nat", "-A", "OUTPUT", "-p", "udp", "-j", "REJECT"],
                    ]
                    for r in rules:
                        subprocess.run(["sudo"] + r)
                
                time.sleep(1)
                self._refresh_state()
            except Exception as e:
                self.status = f"FAILED: {str(e)[:20]}"
                self.is_working = False
                
        threading.Thread(target=_worker, daemon=True).start()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.A:
            self._toggle()
        elif ev.button is Button.Y:
            self._refresh_state()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("PAYLOAD :: ANON_SURF", True, theme.ACCENT), (theme.PADDING, 8))
        
        y = head_h + 40
        x = 50
        
        # Status
        surf.blit(self.f_main.render("MODE:", True, theme.FG_DIM), (x, y))
        color = theme.ACCENT if self.is_active else theme.FG
        surf.blit(self.f_main.render(self.status, True, color), (x + 120, y))
        
        y += 40
        surf.blit(self.f_main.render("PUBLIC_IP:", True, theme.FG_DIM), (x, y))
        ip_color = theme.WARN if self.is_active else theme.FG
        surf.blit(self.f_main.render(self.public_ip, True, ip_color), (x + 120, y))
        
        if self.is_working:
            y += 60
            txt = "COMMENCING ENCRYPTION..." if not self.is_active else "RESTORING UPLINK..."
            surf.blit(self.f_small.render(txt, True, theme.ACCENT), (x, y))

        # Footer
        hint = "A: TOGGLE STEALTH  Y: REFRESH  B: BACK"
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))
