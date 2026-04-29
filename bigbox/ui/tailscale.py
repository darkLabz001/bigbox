"""Tailscale — Secure VPN management and status."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_STATUS = "status"
PHASE_LOGIN = "login"

class TailscaleView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_STATUS
        self.status_msg = "RETRIEVING_DATA"
        
        self.info: Dict[str, Any] = {}
        self.is_loading = False
        self._stop_thread = False
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 20)
        self.f_tiny = pygame.font.Font(None, 16)
        
        self.qr_url: Optional[str] = None
        self.qr_lines: List[str] = []
        
        self._refresh_status()

    def _refresh_status(self):
        def _worker():
            try:
                cmd = ["sudo", "tailscale", "status", "--json"]
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                self.info = json.loads(out)
                self.status_msg = "UPLINK_STABLE"
            except subprocess.CalledProcessError:
                self.info = {}
                self.status_msg = "TAILSCALE_OFFLINE"
            except Exception as e:
                self.status_msg = f"ERROR: {str(e)}"
        
        threading.Thread(target=_worker, daemon=True).start()

    def _start_login(self):
        self.phase = PHASE_LOGIN
        self.is_loading = True
        self.status_msg = "REQUESTING_AUTH_LINK"
        self.qr_lines = []
        self.qr_url = None

        def _worker():
            try:
                # Use sudo as tailscale up needs root to configure networking
                # --timeout=0 prevents it from exiting before we can see the QR
                # We use a short timeout on the process itself if needed, but 
                # tailscale up --qr usually waits for a bit then exits.
                proc = subprocess.Popen(
                    ["sudo", "tailscale", "up", "--qr"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True
                )
                
                output, _ = proc.communicate(timeout=30)
                
                lines = output.splitlines()
                url = None
                qr_content = []
                
                in_qr = False
                for line in lines:
                    if "https://" in line and not url:
                        # Extract URL
                        parts = line.split()
                        for p in parts:
                            if p.startswith("https://"):
                                url = p
                                break
                    
                    # QR code lines usually start with special characters or blocks
                    if "█" in line or (len(line) > 10 and all(c in " █▀▄" for c in line.strip())):
                        in_qr = True
                    
                    if in_qr:
                        qr_content.append(line)
                
                self.qr_lines = qr_content
                self.qr_url = url
                
                if not url and not qr_content:
                    self.status_msg = f"AUTH_ERROR: NO QR DATA"
                    # Log raw output for debugging
                    print(f"[tailscale] Raw output: {output}")
                else:
                    self.status_msg = "SCAN QR TO AUTHENTICATE"
            except subprocess.TimeoutExpired:
                self.status_msg = "AUTH_ERROR: TIMEOUT"
                proc.kill()
            except FileNotFoundError:
                self.status_msg = "ERROR: TAILSCALE NOT INSTALLED"
            except Exception as e:
                self.status_msg = f"AUTH_ERROR: {str(e)}"
            self.is_loading = False
            
        threading.Thread(target=_worker, daemon=True).start()

    def _toggle(self):
        backend_state = self.info.get("BackendState", "NoState")
        cmd = ["sudo", "tailscale", "down" if backend_state == "Running" else "up"]
        
        def _worker():
            try:
                subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
                time.sleep(1)
                self._refresh_status()
            except Exception as e:
                self.status_msg = f"TOGGLE_FAILED: {str(e)}"
        
        threading.Thread(target=_worker, daemon=True).start()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            if self.phase == PHASE_LOGIN:
                self.phase = PHASE_STATUS
                self._refresh_status()
            else:
                self.dismissed = True
            return

        if self.phase == PHASE_STATUS:
            if ev.button is Button.A:
                self._toggle()
            elif ev.button is Button.X:
                self._start_login()
            elif ev.button is Button.Y:
                self._refresh_status()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("SYSTEM :: TAILSCALE_VPN", True, theme.ACCENT), (theme.PADDING, 8))
        
        if self.phase == PHASE_STATUS:
            self._render_status(surf, head_h)
        elif self.phase == PHASE_LOGIN:
            self._render_login(surf, head_h)

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        surf.blit(self.f_small.render(f"STATUS: {self.status_msg}", True, theme.ACCENT), (10, theme.SCREEN_H - 26))
        
        hint = "A: TOGGLE  X: LOGIN  Y: REFRESH  B: BACK" if self.phase == PHASE_STATUS else "B: BACK"
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))

    def _render_status(self, surf: pygame.Surface, head_h: int):
        y = head_h + 30
        x = 50
        
        backend_state = self.info.get("BackendState", "OFFLINE")
        color = theme.ACCENT if backend_state == "Running" else theme.ERR
        
        surf.blit(self.f_main.render("BACKEND_STATE:", True, theme.FG_DIM), (x, y))
        surf.blit(self.f_main.render(backend_state.upper(), True, color), (x + 180, y))
        
        y += 40
        if backend_state == "Running":
            self_info = self.info.get("Self", {})
            ip = self_info.get("TailscaleIPs", ["0.0.0.0"])[0]
            name = self_info.get("DNSName", "unknown").split(".")[0]
            
            surf.blit(self.f_main.render("LOCAL_IP:", True, theme.FG_DIM), (x, y))
            surf.blit(self.f_main.render(ip, True, theme.FG), (x + 180, y))
            y += 35
            surf.blit(self.f_main.render("MACHINE_NAME:", True, theme.FG_DIM), (x, y))
            surf.blit(self.f_main.render(name.upper(), True, theme.FG), (x + 180, y))
            
            y += 50
            peers = self.info.get("Peer", {})
            peer_count = len(peers)
            online_peers = sum(1 for p in peers.values() if p.get("Online"))
            
            surf.blit(self.f_main.render(f"NETWORK_NODES: {online_peers}/{peer_count} ONLINE", True, theme.ACCENT_DIM), (x, y))
            
            y += 40
            # List first 5 peers
            for i, (p_id, p_info) in enumerate(list(peers.items())[:5]):
                p_name = p_info.get("HostName", "peer").split(".")[0]
                p_ip = p_info.get("TailscaleIPs", [""])[0]
                p_online = p_info.get("Online", False)
                p_col = theme.ACCENT if p_online else theme.FG_DIM
                
                surf.blit(self.f_tiny.render(f"• {p_name.upper():<15} {p_ip}", True, p_col), (x + 10, y + i * 20))
        else:
            surf.blit(self.f_main.render("VPN IS CURRENTLY DISCONNECTED", True, theme.FG_DIM), (x, y + 20))
            surf.blit(self.f_small.render("PRESS A TO CONNECT OR X TO AUTHENTICATE", True, theme.ACCENT_DIM), (x, y + 50))

    def _render_login(self, surf: pygame.Surface, head_h: int):
        y = head_h + 10
        x_start = 20
        
        if self.is_loading:
            msg = self.f_main.render("NEGOTIATING HANDSHAKE...", True, theme.ACCENT)
            surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, theme.SCREEN_H//2))
            return

        if self.qr_lines:
            # Render the ASCII QR code
            # We want to center it horizontally
            # Find the widest line to determine center
            max_w = 0
            for line in self.qr_lines:
                lw, _ = self.f_tiny.size(line)
                max_w = max(max_w, lw)
            
            x_qr = (theme.SCREEN_W - max_w) // 2
            qr_y = y
            for line in self.qr_lines:
                q_surf = self.f_tiny.render(line, True, theme.FG)
                surf.blit(q_surf, (x_qr, qr_y))
                qr_y += 10 # Adjust line height for the tiny font
            
            if self.qr_url:
                y_text = theme.SCREEN_H - 80
                u_surf = self.f_small.render("SCAN QR OR VISIT LINK:", True, theme.FG_DIM)
                surf.blit(u_surf, (theme.SCREEN_W//2 - u_surf.get_width()//2, y_text))
                
                link_surf = self.f_small.render(self.qr_url, True, theme.ACCENT)
                surf.blit(link_surf, (theme.SCREEN_W//2 - link_surf.get_width()//2, y_text + 20))
        elif self.status_msg.startswith("AUTH_ERROR"):
            msg = self.f_main.render(self.status_msg, True, theme.ERR)
            surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, theme.SCREEN_H//2))
