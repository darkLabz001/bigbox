"""Data Sniper — Real-time credential and POST data extractor.

Uses Bettercap's sniffing engine to identify and extract credentials 
(HTTP, FTP, TELNET, etc.) and interesting POST data from network traffic.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import pty
import select
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Dict

import pygame

from bigbox import theme, hardware
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_IFACE = "iface"
PHASE_SNIPING = "sniping"
PHASE_DETAIL = "detail"

class Credential:
    def __init__(self, raw: str):
        self.timestamp = datetime.now()
        self.raw = raw
        self.type = "DATA"
        self.source = "Unknown"
        self.data = ""
        
        self._parse()

    def _parse(self):
        # Bettercap patterns
        # [sniff.auth] [ftp] 192.168.1.5:45231 > 192.168.1.10:21  admin : password
        if "auth" in self.raw.lower():
            self.type = "AUTH"
            m = re.search(r'\[(.*?)\]\s+(.*?)\s+>\s+(.*?)\s+(.*)', self.raw)
            if m:
                proto, src, dst, creds = m.groups()
                self.source = f"{src} -> {dst} ({proto})"
                self.data = creds.strip()
        # [sniff.http.post] http://site.com/login [email=test&pass=123]
        elif "post" in self.raw.lower():
            self.type = "POST"
            m = re.search(r'http[s]?://\S+', self.raw)
            if m:
                self.source = m.group(0)
                # Extract bracketed data
                m_data = re.search(r'\[(.*)\]', self.raw)
                if m_data: self.data = m_data.group(1)
        else:
            self.data = self.raw[:100]

class DataSniperView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_IFACE
        self.status_msg = "IDLE"
        
        self.ifaces = hardware.list_wifi_clients()
        self.iface_idx = 0
        self.selected_iface = "wlan0"
        
        self.creds: List[Credential] = []
        self.cred_cursor = 0
        self.cred_scroll = 0
        
        self.proc = None
        self.master_fd = None
        self.slave_fd = None
        self._stop_event = threading.Event()
        self.history = deque(maxlen=100)
        
        self.f_main = pygame.font.Font(None, 22)
        self.f_bold = pygame.font.Font(None, 24)
        self.f_tiny = pygame.font.Font(None, 16)
        
        self.LOOT_FILE = Path("loot/credentials.jsonl")

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if self.phase == PHASE_IFACE:
            if ev.button is Button.UP:
                self.iface_idx = (self.iface_idx - 1) % max(1, len(self.ifaces))
            elif ev.button is Button.DOWN:
                self.iface_idx = (self.iface_idx + 1) % max(1, len(self.ifaces))
            elif ev.button is Button.A:
                if self.ifaces:
                    self.selected_iface = self.ifaces[self.iface_idx]
                    self._start_sniping()
            elif ev.button is Button.B:
                self.dismissed = True

        elif self.phase == PHASE_SNIPING:
            if ev.button is Button.UP:
                self.cred_cursor = (self.cred_cursor - 1) % max(1, len(self.creds))
                self._adjust_scroll()
            elif ev.button is Button.DOWN:
                self.cred_cursor = (self.cred_cursor + 1) % max(1, len(self.creds))
                self._adjust_scroll()
            elif ev.button is Button.A:
                if self.creds: self.phase = PHASE_DETAIL
            elif ev.button is Button.B:
                self._stop_sniping()
                self.phase = PHASE_IFACE

        elif self.phase == PHASE_DETAIL:
            if ev.button in (Button.A, Button.B):
                self.phase = PHASE_SNIPING

    def _adjust_scroll(self):
        visible = 10
        if self.cred_cursor < self.cred_scroll:
            self.cred_scroll = self.cred_cursor
        elif self.cred_cursor >= self.cred_scroll + visible:
            self.cred_scroll = self.cred_cursor - visible + 1

    def _start_sniping(self):
        self.phase = PHASE_SNIPING
        self.status_msg = f"SNIPING_{self.selected_iface}"
        self.creds.clear()
        self.history.clear()
        
        # Bettercap command to sniff credentials
        cmd = [
            "sudo", "bettercap",
            "-iface", self.selected_iface,
            "-no-colors",
            "-eval", "net.sniff on; set net.sniff.verbose true"
        ]
        
        self.master_fd, self.slave_fd = pty.openpty()
        try:
            self.proc = subprocess.Popen(
                cmd, preexec_fn=os.setsid,
                stdin=self.slave_fd, stdout=self.slave_fd, stderr=self.slave_fd
            )
            self._stop_event.clear()
            threading.Thread(target=self._read_output, daemon=True).start()
            
            from bigbox import background as _bg
            _bg.register("data_sniper", f"Data Sniper ({self.selected_iface})", 
                         "Network", stop=self._stop_sniping)
        except Exception as e:
            self.status_msg = f"ERR: {str(e)[:20]}"

    def _read_output(self):
        # Look for [sniff.auth] or [sniff.http.post]
        while not self._stop_event.is_set() and self.master_fd:
            r, _, _ = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 4096).decode("utf-8", "replace")
                    if data:
                        clean = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        for line in clean.splitlines():
                            line = line.strip()
                            if not line: continue
                            self.history.append(line)
                            
                            if "[sniff." in line and ("auth" in line.lower() or "post" in line.lower() or "cookie" in line.lower()):
                                self.creds.append(Credential(line))
                                # Save to loot
                                self._save_loot(line)
                except: break

    def _save_loot(self, line: str):
        self.LOOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(self.LOOT_FILE, "a") as f:
            f.write(f"{datetime.now().isoformat()} | {line}\n")

    def _stop_sniping(self):
        self._stop_event.set()
        if self.proc:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
                time.sleep(0.5)
                self.proc.terminate()
            except: pass
        if self.master_fd: os.close(self.master_fd)
        if self.slave_fd: os.close(self.slave_fd)
        self.proc = self.master_fd = self.slave_fd = None
        from bigbox import background as _bg
        _bg.unregister("data_sniper")

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_bold.render("DATA SNIPER :: INTERCEPTOR", True, theme.ACCENT), (theme.PADDING, 10))

        if self.phase == PHASE_IFACE:
            self._render_iface(surf, head_h)
        elif self.phase == PHASE_SNIPING:
            self._render_sniping(surf, head_h)
        elif self.phase == PHASE_DETAIL:
            self._render_detail(surf, head_h)

        # Footer
        foot_h = 30
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        surf.blit(self.f_tiny.render(f"STATE: {self.status_msg}", True, theme.ACCENT), (15, theme.SCREEN_H - 22))

    def _render_iface(self, surf: pygame.Surface, head_h: int):
        y = head_h + 30
        surf.blit(self.f_main.render("Select target interface:", True, theme.FG), (30, y))
        y += 40
        for i, name in enumerate(self.ifaces):
            sel = i == self.iface_idx
            rect = pygame.Rect(30, y + i*35, 300, 30)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=4)
            surf.blit(self.f_main.render(name, True, theme.ACCENT if sel else theme.FG), (45, rect.y + 5))

    def _render_sniping(self, surf: pygame.Surface, head_h: int):
        # 1. Log view (top half)
        log_h = 100
        log_rect = pygame.Rect(10, head_h + 10, theme.SCREEN_W - 20, log_h)
        pygame.draw.rect(surf, (5, 5, 8), log_rect, border_radius=4)
        pygame.draw.rect(surf, theme.DIVIDER, log_rect, 1, border_radius=4)
        
        lines = list(self.history)[-6:]
        for i, ln in enumerate(lines):
            surf.blit(self.f_tiny.render(ln[:100], True, theme.FG_DIM), (log_rect.x + 10, log_rect.y + 5 + i*15))

        # 2. Creds view (bottom half)
        list_y = log_rect.bottom + 10
        list_h = theme.SCREEN_H - list_y - 40
        pygame.draw.rect(surf, theme.BG_ALT, (10, list_y, theme.SCREEN_W - 20, list_h), border_radius=4)
        
        if not self.creds:
            surf.blit(self.f_main.render("WAITING_FOR_DATA_PACKETS...", True, theme.FG_DIM), (theme.SCREEN_W//2 - 100, list_y + 50))
        else:
            for i in range(10):
                idx = self.cred_scroll + i
                if idx >= len(self.creds): break
                c = self.creds[idx]
                sel = idx == self.cred_cursor
                ry = list_y + i*25
                if sel: pygame.draw.rect(surf, theme.SELECTION_BG, (12, ry, theme.SCREEN_W - 24, 24), border_radius=2)
                
                col = theme.WARN if c.type == "AUTH" else theme.ACCENT
                surf.blit(self.f_tiny.render(f"[{c.type}]", True, col), (15, ry + 5))
                surf.blit(self.f_main.render(c.source[:40], True, theme.FG), (70, ry + 3))
                surf.blit(self.f_tiny.render(c.data[:50], True, theme.FG_DIM), (350, ry + 5))

    def _render_detail(self, surf: pygame.Surface, head_h: int):
        c = self.creds[self.cred_cursor]
        box = pygame.Rect(50, head_h + 50, theme.SCREEN_W - 100, 200)
        pygame.draw.rect(surf, theme.BG_ALT, box, border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT, box, 2, border_radius=8)
        
        surf.blit(self.f_bold.render(f"CAPTURED {c.type}", True, theme.ACCENT), (box.x + 20, box.y + 20))
        surf.blit(self.f_main.render(f"Source: {c.source}", True, theme.FG), (box.x + 20, box.y + 60))
        
        # Wrapped data
        data_txt = f"Data: {c.data}"
        for i in range(0, len(data_txt), 60):
            surf.blit(self.f_main.render(data_txt[i:i+60], True, theme.WARN), (box.x + 20, box.y + 100 + (i//60)*25))
