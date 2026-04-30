"""Bettercap Dashboard v2 — Real-time network combat dashboard.

Launches bettercap in an interactive session, parses its output,
and allows the user to select targets for specific MITM attacks.
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
from bigbox.ui.scroll_list import ScrollList
from bigbox.ui.section import Action

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_DASHBOARD = "dashboard"
PHASE_MENU = "menu"

class BettercapView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_DASHBOARD
        self.proc: Optional[subprocess.Popen] = None
        self.hosts: Dict[str, Dict] = {} # mac -> {ip, vendor, last_seen, is_spoofing}
        self.events: List[str] = []
        self.status = "INITIALIZING_ENGINE"
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 22)
        self.f_log = pygame.font.Font(None, 18)
        self.f_small = pygame.font.Font(None, 18)
        self.f_tiny = pygame.font.Font(None, 14)
        
        self.selected_host_idx = 0
        self.menu_list: Optional[ScrollList] = None
        
        self.is_probing = True
        self.is_sniffing = False
        self.is_dns_spoofing = False
        
        self._start_engine()

    def _start_engine(self):
        # Interactive session with ticker for updates
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
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )
                self.status = "ENGINE_RUNNING"
                
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
                        if mac not in self.hosts:
                            self.hosts[mac] = {"is_spoofing": False}
                        
                        self.hosts[mac].update({
                            "ip": ip,
                            "mac": mac,
                            "vendor": vendor.strip() or "Unknown",
                            "last_seen": time.time()
                        })
                    elif "[at]" in line or "[sys.log]" in line or "[!] " in line:
                        # Event logs (strip bettercap tags)
                        clean = re.sub(r'\[.*?\]', '', line).strip()
                        if clean:
                            self.events.append(clean)
                            if len(self.events) > 50: self.events.pop(0)
                            
            except Exception as e:
                self.status = f"ENGINE_ERROR: {str(e)[:20]}"
            
        threading.Thread(target=_reader, daemon=True).start()

    def _send_cmd(self, cmd: str):
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(f"{cmd}\n")
                self.proc.stdin.flush()
                self.events.append(f"EXEC: {cmd}")
            except:
                pass

    def _stop_engine(self):
        if self.proc:
            self._send_cmd("net.probe off; arp.spoof off; exit")
            self.proc.terminate()
            self.proc = None

    def _open_attack_menu(self):
        hosts = sorted(self.hosts.values(), key=lambda x: x["last_seen"], reverse=True)
        if not hosts or self.selected_host_idx >= len(hosts):
            return
        
        target = hosts[self.selected_host_idx]
        target_ip = target["ip"]
        target_mac = target["mac"]
        
        def toggle_arp(ctx):
            target["is_spoofing"] = not target["is_spoofing"]
            state = "on" if target["is_spoofing"] else "off"
            self._send_cmd(f"set arp.spoof.targets {target_ip}; arp.spoof {state}")
            self.phase = PHASE_DASHBOARD

        def toggle_sniff(ctx):
            self.is_sniffing = not self.is_sniffing
            state = "on" if self.is_sniffing else "off"
            self._send_cmd(f"net.sniff {state}")
            self.phase = PHASE_DASHBOARD

        def toggle_dns(ctx):
            self.is_dns_spoofing = not self.is_dns_spoofing
            state = "on" if self.is_dns_spoofing else "off"
            self._send_cmd(f"dns.spoof {state}")
            self.phase = PHASE_DASHBOARD

        actions = [
            Action(f"ARP SPOOF: {'[ON]' if target['is_spoofing'] else '[OFF]'}", toggle_arp),
            Action(f"SNIFFER: {'[ON]' if self.is_sniffing else '[OFF]'}", toggle_sniff),
            Action(f"DNS SPOOF: {'[ON]' if self.is_dns_spoofing else '[OFF]'}", toggle_dns),
        ]
        
        self.menu_list = ScrollList(actions)
        self.phase = PHASE_MENU

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        
        if ev.button is Button.B:
            if self.phase == PHASE_MENU:
                self.phase = PHASE_DASHBOARD
            else:
                self._stop_engine()
                self.dismissed = True
            return

        if self.phase == PHASE_DASHBOARD:
            hosts_count = len(self.hosts)
            if ev.button is Button.UP:
                self.selected_host_idx = (self.selected_host_idx - 1) % max(1, hosts_count)
            elif ev.button is Button.DOWN:
                self.selected_host_idx = (self.selected_host_idx + 1) % max(1, hosts_count)
            elif ev.button is Button.A:
                self._open_attack_menu()
            elif ev.button is Button.X:
                self._send_cmd("net.clear; net.probe on")
                self.events.append("FLUSHING_HOST_CACHE...")
        
        elif self.phase == PHASE_MENU and self.menu_list:
            self.menu_list.handle(ev, ctx)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("COMBAT :: BETTERCAP_v2", True, theme.ACCENT), (theme.PADDING, 8))
        
        half_w = theme.SCREEN_W // 2
        
        # 1. Hosts
        surf.blit(self.f_main.render("NETWORK_TARGETS", True, theme.FG_DIM), (20, head_h + 10))
        pygame.draw.line(surf, theme.DIVIDER, (20, head_h + 30), (half_w - 20, head_h + 30))
        
        y = head_h + 40
        sorted_hosts = sorted(self.hosts.values(), key=lambda x: x["last_seen"], reverse=True)
        for i, h in enumerate(sorted_hosts[:12]):
            is_selected = (i == self.selected_host_idx)
            is_active = h.get("is_spoofing", False)
            
            # Draw highlight for selected
            if is_selected:
                pygame.draw.rect(surf, (20, 20, 30), (20, y-2, half_w - 40, 22), border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, (20, y-2, half_w - 40, 22), 1, border_radius=4)

            col = theme.FG
            if is_active: col = theme.ERR # Target is being attacked
            elif time.time() - h["last_seen"] < 5: col = theme.ACCENT # Freshly seen
            
            ip_txt = f"{h['ip']:<15}"
            vendor = h['vendor'][:18]
            surf.blit(self.f_main.render(ip_txt, True, col), (25, y))
            surf.blit(self.f_tiny.render(vendor.upper(), True, theme.FG_DIM), (150, y + 4))
            
            if is_active:
                surf.blit(self.f_tiny.render("ATTACKING", True, theme.ERR), (half_w - 100, y + 4))

            y += 22
            
        # 2. Events
        surf.blit(self.f_main.render("COMBAT_LOG", True, theme.FG_DIM), (half_w + 10, head_h + 10))
        pygame.draw.line(surf, theme.DIVIDER, (half_w + 10, head_h + 30), (theme.SCREEN_W - 20, head_h + 30))
        
        y = head_h + 40
        for e in reversed(self.events[-18:]):
            txt = e[:45]
            col = theme.FG
            if "EXEC:" in txt: col = theme.ACCENT_DIM
            elif "!" in txt: col = theme.WARN
            
            surf.blit(self.f_log.render(f"> {txt}", True, col), (half_w + 15, y))
            y += 18

        # Menu Overlay
        if self.phase == PHASE_MENU and self.menu_list:
            overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 200))
            surf.blit(overlay, (0,0))
            
            menu_rect = pygame.Rect(theme.SCREEN_W//4, theme.SCREEN_H//4, theme.SCREEN_W//2, theme.SCREEN_H//2)
            pygame.draw.rect(surf, theme.BG_ALT, menu_rect, border_radius=8)
            pygame.draw.rect(surf, theme.ACCENT, menu_rect, 2, border_radius=8)
            
            header = self.f_main.render("SELECT ATTACK VECTOR", True, theme.ACCENT)
            surf.blit(header, (menu_rect.centerx - header.get_width()//2, menu_rect.y + 10))
            
            list_rect = pygame.Rect(menu_rect.x + 10, menu_rect.y + 40, menu_rect.width - 20, menu_rect.height - 60)
            self.menu_list.render(surf, list_rect, self.f_main)

        # Footer
        if self.phase == PHASE_DASHBOARD:
            pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
            status_col = theme.ACCENT if "RUNNING" in self.status else theme.ERR
            surf.blit(self.f_small.render(f"ENGINE: {self.status}", True, status_col), (10, theme.SCREEN_H - 26))
            
            hint = "UP/DOWN: Target  A: Attack  X: Flush  B: Exit"
            h_surf = self.f_small.render(hint, True, theme.FG_DIM)
            surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))
