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
PHASE_ADVANCED = "advanced"

class TailscaleView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_STATUS
        self.status_msg = "RETRIEVING_DATA"
        
        self.info: Dict[str, Any] = {}
        self.settings = {"accept_dns": True, "exit_node": False}
        self.is_loading = False
        self._stop_thread = False
        
        self.f_title = pygame.font.Font(None, 32)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 20)
        self.f_tiny = pygame.font.Font(None, 16)
        # Use DejaVuSansMono if available for QR codes, otherwise fallback to system mono
        mono_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
        if os.path.exists(mono_path):
            self.f_mono = pygame.font.Font(mono_path, 12)
        else:
            self.f_mono = pygame.font.Font(None, 12)
        
        self.qr_url: Optional[str] = None
        self.qr_lines: List[str] = []
        self.adv_list: Optional[ScrollList] = None
        
        self._refresh_status()

    def _refresh_status(self):
        def _worker():
            try:
                # 1. Get main status
                cmd = ["sudo", "tailscale", "status", "--json"]
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                self.info = json.loads(out)
                self.status_msg = "UPLINK_STABLE"
                
                # 2. Get active settings (prefs)
                pref_out = subprocess.check_output(["sudo", "tailscale", "debug", "prefs"], text=True, stderr=subprocess.DEVNULL)
                # Crude parse since it's not JSON
                self.settings["accept_dns"] = "CorpDNS: true" in pref_out
                self.settings["exit_node"] = "AdvertiseExitNode: true" in pref_out
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
        
        # If logged out, redirect A button to the login flow
        if backend_state in ("NoState", "NeedsLogin"):
            self._start_login()
            return

        # Simple Start/Stop for already-authenticated nodes
        cmd = ["sudo", "tailscale", "down" if backend_state == "Running" else "up", "--timeout=5s"]
        
        def _worker():
            self.is_loading = True
            try:
                subprocess.run(cmd, check=True, stderr=subprocess.DEVNULL)
                time.sleep(1)
                self._refresh_status()
            except Exception as e:
                self.status_msg = f"TOGGLE_FAILED: {str(e)}"
            self.is_loading = False
        
        threading.Thread(target=_worker, daemon=True).start()

    def _logout(self):
        self.is_loading = True
        self.status_msg = "LOGGING_OUT..."
        def _worker():
            try:
                subprocess.run(["sudo", "tailscale", "logout"], check=True)
                time.sleep(1)
                self._refresh_status()
            except Exception as e:
                self.status_msg = f"LOGOUT_FAILED: {str(e)}"
            self.is_loading = False
        threading.Thread(target=_worker, daemon=True).start()

    def _update_tailscale_settings(self, ctx=None):
        self.is_loading = True
        self.status_msg = "APPLYING_PREFS..."
        def _worker():
            try:
                dns = "true" if self.settings["accept_dns"] else "false"
                exit_node = "--advertise-exit-node" if self.settings["exit_node"] else ""
                cmd = ["sudo", "tailscale", "up", f"--accept-dns={dns}"]
                if exit_node: cmd.append(exit_node)
                subprocess.run(cmd, check=True)
                time.sleep(1)
                self._refresh_status()
            except Exception as e:
                self.status_msg = f"PREFS_FAILED: {str(e)}"
            self.is_loading = False
            self.phase = PHASE_STATUS
        threading.Thread(target=_worker, daemon=True).start()

    def _open_advanced_menu(self):
        def toggle_dns(ctx):
            self.settings["accept_dns"] = not self.settings["accept_dns"]
            self._open_advanced_menu() # Refresh list

        def toggle_exit(ctx):
            self.settings["exit_node"] = not self.settings["exit_node"]
            self._open_advanced_menu()

        actions = [
            Action(f"ACCEPT DNS: {'[ON]' if self.settings['accept_dns'] else '[OFF]'}", toggle_dns),
            Action(f"EXIT NODE: {'[ON]' if self.settings['exit_node'] else '[OFF]'}", toggle_exit),
            Action("APPLY CHANGES", self._update_tailscale_settings),
        ]
        self.adv_list = ScrollList(actions)
        self.phase = PHASE_ADVANCED

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            if self.phase in (PHASE_LOGIN, PHASE_ADVANCED):
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
                if Button.HK in ctx.held_buttons:
                    self._logout()
                else:
                    self._open_advanced_menu()
        
        elif self.phase == PHASE_ADVANCED and self.adv_list:
            self.adv_list.handle(ev, ctx)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("SYSTEM :: TAILSCALE_VPN", True, theme.ACCENT), (theme.PADDING, 8))
        
        if self.phase == PHASE_STATUS:
            self._render_status(surf, head_h)
            if self.is_loading:
                # Dim the screen slightly and show a loading msg
                overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H - head_h - 35), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 150))
                surf.blit(overlay, (0, head_h))
                msg = self.f_main.render("PROCESSING...", True, theme.ACCENT)
                surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, theme.SCREEN_H//2))
        elif self.phase == PHASE_LOGIN:
            self._render_login(surf, head_h)
        elif self.phase == PHASE_ADVANCED and self.adv_list:
            # Render a modal-style list
            overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            surf.blit(overlay, (0, 0))
            
            menu_rect = pygame.Rect(theme.SCREEN_W//4, theme.SCREEN_H//4, theme.SCREEN_W//2, theme.SCREEN_H//2)
            pygame.draw.rect(surf, theme.BG_ALT, menu_rect, border_radius=8)
            pygame.draw.rect(surf, theme.ACCENT, menu_rect, 2, border_radius=8)
            
            header = self.f_main.render("ADVANCED SETTINGS", True, theme.ACCENT)
            surf.blit(header, (menu_rect.centerx - header.get_width()//2, menu_rect.y + 10))
            
            list_rect = pygame.Rect(menu_rect.x + 10, menu_rect.y + 40, menu_rect.width - 20, menu_rect.height - 60)
            self.adv_list.render(surf, list_rect, self.f_main)

        # Footer
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        surf.blit(self.f_small.render(f"STATUS: {self.status_msg}", True, theme.ACCENT), (10, theme.SCREEN_H - 26))
        
        if self.phase == PHASE_STATUS:
            hint = "A: TOGGLE  X: LOGIN  Y: ADVANCED  HK+Y: LOGOUT  B: BACK"
        elif self.phase == PHASE_ADVANCED:
            hint = "UP/DOWN: SELECT  A: TOGGLE/APPLY  B: BACK"
        else:
            hint = "B: BACK"
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 26))

    def _render_status(self, surf: pygame.Surface, head_h: int):
        y = head_h + 30
        x = 50
        
        backend_state = self.info.get("BackendState", "OFFLINE")
        is_running = backend_state == "Running"
        needs_login = backend_state in ("NoState", "NeedsLogin")
        
        color = theme.ACCENT if is_running else (theme.WARN if needs_login else theme.ERR)
        
        surf.blit(self.f_main.render("BACKEND_STATE:", True, theme.FG_DIM), (x, y))
        display_state = "LOGGED_OUT" if needs_login else backend_state.upper()
        surf.blit(self.f_main.render(display_state, True, color), (x + 180, y))
        
        # Explicit Start/Stop indicator
        if needs_login:
            action_text = "LOGIN (A)"
        else:
            action_text = "STOP (A)" if is_running else "START (A)"
        surf.blit(self.f_small.render(action_text, True, theme.ACCENT_DIM), (x + 350, y))

        y += 40
        if is_running:
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
        
        if self.is_loading:
            msg = self.f_main.render("NEGOTIATING HANDSHAKE...", True, theme.ACCENT)
            surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, theme.SCREEN_H//2))
            return

        if self.qr_lines:
            # Render the ASCII QR code with monospaced font
            max_w = 0
            for line in self.qr_lines:
                lw, _ = self.f_mono.size(line)
                max_w = max(max_w, lw)
            
            x_qr = (theme.SCREEN_W - max_w) // 2
            qr_y = head_h + 5
            for line in self.qr_lines:
                q_surf = self.f_mono.render(line, True, theme.FG)
                surf.blit(q_surf, (x_qr, qr_y))
                qr_y += 11 # Monospaced line height
            
            if self.qr_url:
                y_text = theme.SCREEN_H - 85
                u_surf = self.f_small.render("SCAN QR OR VISIT LINK:", True, theme.FG_DIM)
                surf.blit(u_surf, (theme.SCREEN_W//2 - u_surf.get_width()//2, y_text))
                
                link_surf = self.f_small.render(self.qr_url, True, theme.ACCENT)
                surf.blit(link_surf, (theme.SCREEN_W//2 - link_surf.get_width()//2, y_text + 20))
        elif self.status_msg.startswith("AUTH_ERROR"):
            msg = self.f_main.render(self.status_msg, True, theme.ERR)
            surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, theme.SCREEN_H//2))
