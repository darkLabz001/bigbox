"""ARP Scan Tool — local network host discovery with interface selection."""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


@dataclass
class ArpDevice:
    ip: str
    mac: str
    vendor: str = "Unknown"


class ARPScanView:
    """Dedicated UI for running arp-scan on local interfaces."""

    def __init__(self) -> None:
        self.interfaces = self._get_interfaces()
        self.selected_iface = self.interfaces[0] if self.interfaces else "wlan0"
        self.devices: list[ArpDevice] = []
        self.scanning = False
        self.dismissed = False
        self.status_msg = "READY"
        
        self.input_mode = True # Select interface first
        self._stop_scan = False
        self._proc: subprocess.Popen | None = None
        self._scan_thread: threading.Thread | None = None
        self._scroll_y = 0
        self._cursor = 0 # for interface selection

    def _get_interfaces(self) -> list[str]:
        """Get list of network interfaces using ip link."""
        try:
            out = subprocess.check_output(["ip", "-o", "link", "show"], text=True)
            ifaces = []
            for line in out.splitlines():
                # Format: 2: eth0: <BROADCAST...
                match = re.search(r'\d+: (.*?):', line)
                if match:
                    name = match.group(1)
                    if name != "lo":
                        ifaces.append(name)
            return ifaces if ifaces else ["wlan0", "eth0"]
        except Exception:
            return ["wlan0", "eth0", "usb0"]

    def _start_scan(self):
        self.devices.clear()
        self.scanning = True
        self.input_mode = False
        self._stop_scan = False
        self._scroll_y = 0
        self.status_msg = f"SCANNING {self.selected_iface}..."
        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()

    def _scan_worker(self):
        """Runs arp-scan and parses output."""
        try:
            # -I for interface, --localnet for auto-range
            cmd = ["arp-scan", "-I", self.selected_iface, "--localnet"]
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            
            if self._proc.stdout:
                for line in self._proc.stdout:
                    if self._stop_scan:
                        break
                    
                    # Parse: 192.168.1.1  00:11:22:33:44:55  Vendor Name
                    match = re.search(r'(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:]{17})\s+(.*)', line)
                    if match:
                        ip, mac, vendor = match.groups()
                        dev = ArpDevice(ip=ip, mac=mac, vendor=vendor.strip())
                        self.devices.append(dev)
                        
                        # Auto-scroll
                        max_scroll = max(0, len(self.devices) * 45 - 300)
                        if self._scroll_y >= max_scroll - 90:
                            self._scroll_y = max_scroll
            
            if self._proc:
                self._proc.wait(timeout=1.0)
                if not self._stop_scan:
                    self.status_msg = "SCAN COMPLETE"
        except Exception as e:
            if not self._stop_scan:
                self.status_msg = f"ERROR: {str(e)[:20]}"
        finally:
            self.scanning = False
            self._proc = None

    def handle(self, ev: ButtonEvent, ctx: SectionContext | None = None) -> None:
        if not ev.pressed: return
        
        if ev.button is Button.B:
            if self.scanning:
                self._stop_scan = True
                if self._proc:
                    try: self._proc.kill()
                    except Exception: pass
                self.status_msg = "SCAN CANCELED"
                self.scanning = False
            elif not self.input_mode:
                self.input_mode = True
                self.devices.clear()
                self.status_msg = "READY"
            else:
                self.dismissed = True
        
        elif self.input_mode:
            if ev.button is Button.UP:
                self._cursor = (self._cursor - 1) % len(self.interfaces)
                self.selected_iface = self.interfaces[self._cursor]
            elif ev.button is Button.DOWN:
                self._cursor = (self._cursor + 1) % len(self.interfaces)
                self.selected_iface = self.interfaces[self._cursor]
            elif ev.button is Button.A:
                self._start_scan()
            elif ev.button is Button.X and ctx:
                # Custom Target Range instead of --localnet
                def _on_input(val: str | None):
                    if val:
                        self._start_custom_scan(val)
                ctx.get_input("ENTER SCAN TARGET (e.g. 10.0.0.0/24)", _on_input)
        
        else: # Results mode
            if ev.button is Button.UP:
                self._scroll_y = max(0, self._scroll_y - 40)
            elif ev.button is Button.DOWN:
                max_scroll = max(0, len(self.devices) * 45 - 300)
                self._scroll_y = min(max_scroll, self._scroll_y + 40)

    def _start_custom_scan(self, target: str):
        self.devices.clear()
        self.scanning = True
        self.input_mode = False
        self._stop_scan = False
        self.status_msg = f"SCANNING {target}..."
        
        def _custom_worker():
            try:
                cmd = ["arp-scan", "-I", self.selected_iface, target]
                self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                if self._proc.stdout:
                    for line in self._proc.stdout:
                        if self._stop_scan: break
                        match = re.search(r'(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:]{17})\s+(.*)', line)
                        if match:
                            ip, mac, vendor = match.groups()
                            self.devices.append(ArpDevice(ip=ip, mac=mac, vendor=vendor.strip()))
                self._proc.wait(timeout=1.0)
                self.status_msg = "SCAN COMPLETE"
            except Exception: pass
            finally: self.scanning = False
            
        self._scan_thread = threading.Thread(target=_custom_worker, daemon=True)
        self._scan_thread.start()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("RECON :: ARP_SCAN", True, theme.ACCENT), (theme.PADDING, 8))

        # Status Bar
        pygame.draw.rect(surf, (10, 10, 20), (0, theme.SCREEN_H - 35, theme.SCREEN_W, 35))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - 35), (theme.SCREEN_W, theme.SCREEN_H - 35))
        f_small = pygame.font.Font(None, 22)
        status = f_small.render(f"IFACE: {self.selected_iface} | {self.status_msg}", True, theme.ACCENT)
        surf.blit(status, (theme.PADDING, theme.SCREEN_H - 28))

        if self.input_mode:
            self._render_input(surf, head_h)
        else:
            self._render_results(surf, head_h)

    def _render_input(self, surf: pygame.Surface, offset_y: int):
        f_big = pygame.font.Font(None, 38)
        f_med = pygame.font.Font(None, 28)
        
        msg = f_big.render("SELECT INTERFACE", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W//2 - msg.get_width()//2, offset_y + 50))
        
        # Interface List
        list_y = offset_y + 100
        for i, iface in enumerate(self.interfaces):
            sel = (i == self._cursor)
            rect = pygame.Rect(theme.SCREEN_W//2 - 150, list_y + i*50, 300, 45)
            if sel:
                pygame.draw.rect(surf, (20, 40, 60), rect, border_radius=5)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=5)
            
            color = theme.ACCENT if sel else theme.FG_DIM
            txt = f_big.render(iface, True, color)
            surf.blit(txt, (rect.centerx - txt.get_width()//2, rect.centery - txt.get_height()//2))

        hint = f_med.render("A: LOCAL SCAN  X: CUSTOM TARGET  B: BACK", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W//2 - hint.get_width()//2, theme.SCREEN_H - 80))

    def _render_results(self, surf: pygame.Surface, offset_y: int):
        res_rect = pygame.Rect(theme.PADDING, offset_y + 10, theme.SCREEN_W - 2*theme.PADDING, theme.SCREEN_H - offset_y - 50)
        pygame.draw.rect(surf, (5, 5, 10), res_rect)
        pygame.draw.rect(surf, theme.DIVIDER, res_rect, 1)
        
        f_body = pygame.font.Font(None, 26)
        f_vendor = pygame.font.Font(None, 20)
        
        list_rect = res_rect.inflate(-20, -20)
        
        for i, dev in enumerate(self.devices):
            y = list_rect.y + i * 45 - self._scroll_y
            if y < list_rect.y - 40: continue
            if y > list_rect.bottom: break
            
            if i % 2 == 0:
                pygame.draw.rect(surf, (15, 20, 30), (list_rect.x, y-5, list_rect.width, 40))
            
            ip_txt = f_body.render(dev.ip, True, theme.FG)
            mac_txt = f_body.render(dev.mac, True, theme.FG_DIM)
            vendor_txt = f_vendor.render(dev.vendor[:40], True, theme.ACCENT)
            
            surf.blit(ip_txt, (list_rect.x + 5, y))
            surf.blit(mac_txt, (list_rect.x + 180, y))
            surf.blit(vendor_txt, (list_rect.x + 5, y + 20))
            
            pygame.draw.circle(surf, (0, 255, 100), (list_rect.right - 20, y + 10), 6)

        if self.scanning:
            scan_y = res_rect.y + (int(time.time() * 150) % res_rect.height)
            pygame.draw.line(surf, (0, 255, 0, 150), (res_rect.left, scan_y), (res_rect.right, scan_y), 3)

        f_hint = pygame.font.Font(None, 20)
        hint_text = "PRESS B TO STOP SCAN" if self.scanning else "UP/DOWN: Scroll  B: BACK"
        hint = f_hint.render(hint_text, True, theme.ERR if self.scanning else theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - 20, theme.SCREEN_H - 28))
