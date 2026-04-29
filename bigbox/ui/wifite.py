"""Wifite — Interactive automated wireless auditor with high-fidelity UI."""
from __future__ import annotations

import os
import math
import signal
import subprocess
import threading
import pty
import select
import time
from collections import deque
from typing import TYPE_CHECKING, List, Optional

import pygame

from bigbox import theme, hardware
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_LANDING = "landing"
PHASE_CONFIG = "config"
PHASE_RUNNING = "running"

class WifiteView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.history = deque(maxlen=300)
        self.status_msg = "INITIALIZING CORE..."
        
        # UI dimensions
        self.font_size = 18
        self.font = pygame.font.Font(None, self.font_size)
        self.f_title = pygame.font.Font(None, 42)
        self.f_med = pygame.font.Font(None, 24)
        self.f_tiny = pygame.font.Font(None, 16)
        
        # Attack Options (Toggles)
        self.opt_wps = True
        self.opt_wpa = True
        self.opt_pmkid = True
        self.opt_pixie = True
        self.opt_kill = True
        self.custom_args = ""
        
        self.selected_iface: Optional[str] = None
        active = hardware.list_wifi_clients() + hardware.list_monitor_ifaces()
        if "wlan0" in active: self.selected_iface = "wlan0"
        elif active: self.selected_iface = active[0]
        else: self.selected_iface = "wlan0"

        # Process management
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None
        
        self.cursor = 0 
        self.is_scanning = False
        self.scroll_idx = 0
        
        # Aesthetics
        self._grid_surf = self._create_grid_bg()
        self._scan_y = 0

    def _create_grid_bg(self) -> pygame.Surface:
        s = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H))
        s.fill(theme.BG)
        for x in range(0, theme.SCREEN_W, 40):
            pygame.draw.line(s, (15, 15, 25), (x, 0), (x, theme.SCREEN_H))
        for y in range(0, theme.SCREEN_H, 40):
            pygame.draw.line(s, (15, 15, 25), (0, y), (theme.SCREEN_W, y))
        return s

    def _get_full_args(self) -> List[str]:
        args = ["--dict", "/usr/share/wordlists/rockyou.txt"]
        if self.opt_wps: args.append("--wps")
        if self.opt_wpa: args.append("--wpa")
        if self.opt_pmkid: args.append("--pmkid")
        if self.opt_pixie: args.append("--pixie")
        if self.opt_kill: args.append("--kill")
        if self.custom_args:
            args.extend(self.custom_args.split())
        return args

    def _start_wifite(self):
        wordlist = "/usr/share/wordlists/rockyou.txt"
        if not os.path.exists(wordlist):
            self.status_msg = "ERROR: Wordlist missing"
            return

        self.phase = PHASE_RUNNING
        self.is_scanning = True
        self.history.clear()
        
        cmd = ["wifite", "-i", self.selected_iface] + self._get_full_args()
        self.history.append(f"[SYSTEM] EXECUTING: {' '.join(cmd)}")
        
        self.master_fd, self.slave_fd = pty.openpty()
        try:
            self.process = subprocess.Popen(
                cmd,
                preexec_fn=os.setsid,
                stdin=self.slave_fd,
                stdout=self.slave_fd,
                stderr=self.slave_fd,
                env=os.environ
            )
            self._stop_event.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            self.status_msg = "SCANNING ELECTROMAGNETIC SPECTRUM..."
        except Exception as e:
            self.status_msg = f"LAUNCH ERROR: {e}"

    def _read_output(self):
        while not self._stop_event.is_set() and self.master_fd:
            r, w, e = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 1024).decode("utf-8", "replace")
                    if data:
                        import re
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        was_at_bottom = self.scroll_idx >= len(self.history) - 1
                        for line in clean_data.splitlines():
                            if line.strip():
                                self.history.append(line)
                                if "select target" in line.lower() or "enter number" in line.lower():
                                    self.is_scanning = False
                                    self.status_msg = "SPECTRUM LOCKED. SELECT TARGETS."
                        
                        if was_at_bottom:
                            self.scroll_idx = max(0, len(self.history) - 1)
                except OSError:
                    break

    def _send_input(self, text: str):
        if self.master_fd and text:
            os.write(self.master_fd, (text + "\n").encode())

    def _send_ctrl_c(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                self.is_scanning = False
                self.status_msg = "INTERRUPT SENT. AWAITING PROMPT..."
            except Exception as e:
                print(f"[wifite] ctrl-c error: {e}")

    def _cleanup(self):
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                time.sleep(0.5)
                if self.process.poll() is None:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except: pass
        if self.master_fd:
            try: os.close(self.master_fd)
            except: pass
        if self.slave_fd:
            try: os.close(self.slave_fd)
            except: pass
        self.master_fd = self.slave_fd = self.process = None
        self.is_scanning = False
        self.scroll_idx = 0
        hardware.ensure_wifi_managed()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            if self.phase == PHASE_RUNNING:
                self._cleanup()
                self.phase = PHASE_LANDING
                self.status_msg = "SESSION TERMINATED"
            elif self.phase == PHASE_CONFIG: self.phase = PHASE_LANDING
            else: self.dismissed = True
            return
        if self.phase == PHASE_LANDING:
            if ev.button is Button.A: self._start_wifite()
            elif ev.button is Button.X: self.phase = PHASE_CONFIG
        elif self.phase == PHASE_CONFIG:
            if ev.button is Button.UP: self.cursor = (self.cursor - 1) % 7
            elif ev.button is Button.DOWN: self.cursor = (self.cursor + 1) % 7
            elif ev.button is Button.A: self._toggle_config_option(ctx)
            elif ev.button is Button.START: self.phase = PHASE_LANDING
        elif self.phase == PHASE_RUNNING:
            if ev.button in (Button.A, Button.RR):
                ctx.get_input("SYSTEM INPUT", self._on_terminal_input)
            elif ev.button is Button.UP: self.scroll_idx = max(0, self.scroll_idx - 1)
            elif ev.button is Button.DOWN: self.scroll_idx = min(len(self.history) - 1, self.scroll_idx + 1)
            elif ev.button is Button.LL:
                if self.is_scanning: self._send_ctrl_c()
            elif ev.button is Button.X: self._send_ctrl_c()
            elif ev.button is Button.Y:
                self.history.clear()
                self.scroll_idx = 0

    def _toggle_config_option(self, ctx: App):
        if self.cursor == 0:
            common = ["wlan0", "wlan1", "wlan0mon", "wlan1mon"]
            active = hardware.list_wifi_clients() + hardware.list_monitor_ifaces()
            ifaces = sorted(list(set(common + active)))
            idx = (ifaces.index(self.selected_iface) + 1) % len(ifaces) if self.selected_iface in ifaces else 0
            self.selected_iface = ifaces[idx]
        elif self.cursor == 1: self.opt_wps = not self.opt_wps
        elif self.cursor == 2: self.opt_wpa = not self.opt_wpa
        elif self.cursor == 3: self.opt_pmkid = not self.opt_pmkid
        elif self.cursor == 4: self.opt_pixie = not self.opt_pixie
        elif self.cursor == 5: self.opt_kill = not self.opt_kill
        elif self.cursor == 6:
            ctx.get_input("CUSTOM ARGS", lambda v: setattr(self, "custom_args", v or ""), initial=self.custom_args)

    def _on_terminal_input(self, text: str | None):
        if text is not None: self._send_input(text)

    def render(self, surf: pygame.Surface) -> None:
        surf.blit(self._grid_surf, (0, 0))
        if self.is_scanning:
            self._scan_y = (self._scan_y + 4) % theme.SCREEN_H
            pygame.draw.line(surf, (0, 40, 20), (0, self._scan_y), (theme.SCREEN_W, self._scan_y), 2)
        
        head_h = 60
        pygame.draw.rect(surf, (10, 10, 20), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        # Stylized Header
        tag = self.f_med.render("AUTOMATED AUDITOR", True, theme.ACCENT_DIM)
        surf.blit(tag, (theme.PADDING, 6))
        title = self.f_title.render("WIFITE :: GHOST-2", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, 22))

        if self.phase == PHASE_LANDING: self._render_landing(surf, head_h)
        elif self.phase == PHASE_CONFIG: self._render_config(surf, head_h)
        elif self.phase == PHASE_RUNNING: self._render_terminal(surf, head_h)

        # Footer
        foot_h = 30
        pygame.draw.rect(surf, (5, 5, 10), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        
        status_col = theme.ACCENT if not "ERROR" in self.status_msg else theme.ERR
        surf.blit(self.f_med.render(self.status_msg, True, status_col), (10, theme.SCREEN_H - 25))
        h_surf = self.f_med.render(self._get_hint(), True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 25))

    def _get_hint(self) -> str:
        if self.phase == PHASE_LANDING: return "A: INITIATE  X: CONFIG  B: BACK"
        if self.phase == PHASE_CONFIG: return "UP/DN: SELECT  A: TOGGLE  START: DONE"
        if self.phase == PHASE_RUNNING:
            if self.is_scanning: return "LL: STOP SCAN  UP/DN: SCROLL  B: EXIT"
            return "A: TARGET #  UP/DN: SCROLL  X: SKIP  B: EXIT"
        return "B: BACK"

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        y = head_h + 40
        # Big icon or decorative box
        box_rect = pygame.Rect(theme.SCREEN_W // 2 - 250, y, 500, 200)
        pygame.draw.rect(surf, (20, 20, 30), box_rect, border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT_DIM, box_rect, 1, border_radius=8)
        
        attacks = []
        if self.opt_wps: attacks.append("WPS")
        if self.opt_pixie: attacks.append("PIXIE")
        if self.opt_wpa: attacks.append("WPA")
        if self.opt_pmkid: attacks.append("PMKID")
        
        lines = [
            f"TARGET ADAPTER: {self.selected_iface}",
            f"ACTIVE PAYLOADS: {', '.join(attacks) or 'NONE'}",
            f"CUSTOM STRING: {self.custom_args or 'DEFAULT'}",
            "",
            "READY FOR SIGNAL ACQUISITION",
        ]
        for i, ln in enumerate(lines):
            col = theme.ACCENT if "READY" in ln else theme.FG
            surf.blit(self.f_med.render(ln, True, col), (box_rect.x + 30, box_rect.y + 30 + i * 35))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        y = head_h + 30
        opts = [
            ("INTERFACE", self.selected_iface),
            ("ATTACK WPS", "YES" if self.opt_wps else "NO"),
            ("ATTACK WPA", "YES" if self.opt_wpa else "NO"),
            ("ATTACK PMKID", "YES" if self.opt_pmkid else "NO"),
            ("PIXIE-DUST", "YES" if self.opt_pixie else "NO"),
            ("KILL CONFLICTS", "YES" if self.opt_kill else "NO"),
            ("CUSTOM ARGS", self.custom_args or "(none)"),
        ]
        for i, (lbl, val) in enumerate(opts):
            sel = i == self.cursor
            color = theme.ACCENT if sel else theme.FG
            if sel: 
                pygame.draw.rect(surf, (30, 30, 50), (40, y + i*40, 500, 35), border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT_DIM, (40, y + i*40, 500, 35), 1, border_radius=4)
            surf.blit(self.f_med.render(f"{lbl}:", True, theme.FG_DIM), (60, y + 8 + i*40))
            surf.blit(self.f_med.render(str(val), True, color), (240, y + 8 + i*40))

    def _render_terminal(self, surf: pygame.Surface, head_h: int):
        term_rect = pygame.Rect(10, head_h + 10, theme.SCREEN_W - 20, theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (5, 5, 10, 200), term_rect)
        pygame.draw.rect(surf, theme.DIVIDER, term_rect, 1)
        
        line_h = self.font_size + 2
        max_lines = term_rect.height // line_h
        total = len(self.history)
        
        start = max(0, min(self.scroll_idx, total - max_lines))
        visible_lines = list(self.history)[start : start + max_lines]
        
        if total > max_lines:
            sb_w = 4
            sb_h = max(20, int(term_rect.height * (max_lines / total)))
            sb_y = term_rect.y + int((start / total) * term_rect.height)
            pygame.draw.rect(surf, theme.ACCENT_DIM, (term_rect.right - sb_w - 2, sb_y, sb_w, sb_h))

        for i, line in enumerate(visible_lines):
            # Dynamic coloring based on content
            color = (220, 220, 220)
            if "WPA" in line or "WPS" in line: color = theme.ACCENT
            if "error" in line.lower() or "fail" in line.lower(): color = theme.ERR
            if "found" in line.lower() or "target" in line.lower(): color = (100, 255, 100)
            
            surf.blit(self.f_med.render(line[:100], True, color), (term_rect.x + 10, term_rect.y + 5 + i * line_h))
