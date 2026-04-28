"""Wifite — Interactive automated wireless auditor."""
from __future__ import annotations

import os
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
        self.status_msg = "Automated Wireless Auditor"
        
        # UI dimensions
        self.font_size = 18
        self.font = pygame.font.Font(None, self.font_size)
        
        # Config
        self.args = ["--dict", "/usr/share/wordlists/rockyou.txt", "--kill"]
        self.selected_iface: Optional[str] = None
        clients = hardware.list_wifi_clients()
        if clients: self.selected_iface = clients[0]

        # Process management
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None

    def _start_wifite(self):
        if not self.selected_iface:
            self.status_msg = "ERROR: No Wi-Fi adapter found"
            return

        self.phase = PHASE_RUNNING
        self.history.clear()
        self.history.append(f"Starting Wifite on {self.selected_iface}...")
        
        # Build command
        cmd = ["wifite", "-i", self.selected_iface] + self.args
        
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
        except FileNotFoundError:
            self.history.append("ERROR: 'wifite' command not found. Install with 'sudo apt install wifite'")
            self.status_msg = "Wifite not found"

    def _read_output(self):
        while not self._stop_event.is_set() and self.master_fd:
            r, w, e = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 1024).decode("utf-8", "replace")
                    if data:
                        import re
                        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
                        clean_data = ansi_escape.sub('', data)
                        for line in clean_data.splitlines():
                            if line.strip():
                                self.history.append(line)
                except OSError:
                    break

    def _send_input(self, text: str):
        if self.master_fd and text:
            os.write(self.master_fd, (text + "\n").encode())

    def _send_ctrl_c(self):
        if self.master_fd:
            os.write(self.master_fd, b'\x03')

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
        
        # Ensure monitor mode is disabled and wifi is managed
        hardware.ensure_wifi_managed()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if ev.button is Button.B:
            if self.phase == PHASE_RUNNING:
                self._cleanup()
                self.phase = PHASE_LANDING
                self.status_msg = "Stopped"
            elif self.phase == PHASE_CONFIG:
                self.phase = PHASE_LANDING
            else:
                self.dismissed = True
            return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                self._start_wifite()
            elif ev.button is Button.X:
                self.phase = PHASE_CONFIG
            elif ev.button is Button.Y:
                # Refresh interfaces
                clients = hardware.list_wifi_clients()
                if clients: self.selected_iface = clients[0]
                self.status_msg = f"Adapter refreshed: {self.selected_iface}"

        elif self.phase == PHASE_CONFIG:
            if ev.button is Button.A:
                # Toggle interface if multiple
                ifaces = hardware.list_wifi_clients()
                if ifaces:
                    try:
                        idx = (ifaces.index(self.selected_iface) + 1) % len(ifaces)
                        self.selected_iface = ifaces[idx]
                    except ValueError:
                        self.selected_iface = ifaces[0]
            elif ev.button is Button.X:
                ctx.get_input("Wifite Args", self._on_args_done, initial=" ".join(self.args))
            elif ev.button is Button.START:
                self.phase = PHASE_LANDING

        elif self.phase == PHASE_RUNNING:
            if ev.button is Button.A:
                ctx.get_input("Target / Command", self._on_terminal_input)
            elif ev.button is Button.X:
                self._send_ctrl_c()
            elif ev.button is Button.Y:
                # Clear screen
                self.history.clear()

    def _on_args_done(self, text: str | None):
        if text is not None:
            self.args = text.split()

    def _on_terminal_input(self, text: str | None):
        if text is not None:
            self._send_input(text)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill((0, 0, 0))
        
        # Header
        head_h = 44
        pygame.draw.rect(surf, (20, 20, 20), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        f_title = pygame.font.Font(None, 32)
        title_text = f"WIRELESS :: WIFITE"
        if self.phase == PHASE_RUNNING: title_text += " [ACTIVE]"
        surf.blit(f_title.render(title_text, True, theme.ACCENT), (theme.PADDING, 8))

        if self.phase == PHASE_LANDING:
            self._render_landing(surf, head_h)
        elif self.phase == PHASE_CONFIG:
            self._render_config(surf, head_h)
        elif self.phase == PHASE_RUNNING:
            self._render_terminal(surf, head_h)

        # Status Bar
        foot_h = 30
        pygame.draw.rect(surf, (10, 10, 15), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        surf.blit(self.font.render(self.status_msg, True, theme.ACCENT), (10, theme.SCREEN_H - 22))
        
        hint = self._get_hint()
        h_surf = self.font.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 22))

    def _get_hint(self) -> str:
        if self.phase == PHASE_LANDING: return "A: Start  X: Config  B: Back"
        if self.phase == PHASE_CONFIG: return "A: Iface  X: Args  START: Done"
        if self.phase == PHASE_RUNNING: return "A: Input  X: Ctrl+C  Y: Clear  B: Stop"
        return "B: Back"

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        f_big = pygame.font.Font(None, 40)
        f_med = pygame.font.Font(None, 24)
        
        y = head_h + 80
        msg = f_big.render("WIFITE 2", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, y))
        
        lines = [
            "Automated wireless attack tool.",
            "Attacks WPS, WPA handshakes, and PMKID.",
            "",
            f"Selected Iface: {self.selected_iface or 'NONE'}",
            f"Arguments: {' '.join(self.args)}",
            "",
            "Press A to start the auditor."
        ]
        for i, ln in enumerate(lines):
            col = theme.ACCENT if "A to start" in ln else theme.FG_DIM
            s = f_med.render(ln, True, col)
            surf.blit(s, (theme.SCREEN_W // 2 - s.get_width() // 2, y + 60 + i * 25))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        f_med = pygame.font.Font(None, 32)
        y = head_h + 40
        surf.blit(f_med.render("CONFIGURATION", True, theme.ACCENT), (50, y))
        
        opts = [
            f"A: Interface: {self.selected_iface or 'NONE'}",
            f"X: Custom Args: {' '.join(self.args)}",
            "",
            "START: Save and Return",
            "B: Cancel"
        ]
        for i, opt in enumerate(opts):
            s = self.font.render(opt, True, theme.FG)
            surf.blit(s, (60, y + 60 + i * 40))

    def _render_terminal(self, surf: pygame.Surface, head_h: int):
        # Draw background terminal area
        term_rect = pygame.Rect(5, head_h + 5, theme.SCREEN_W - 10, theme.SCREEN_H - head_h - 40)
        pygame.draw.rect(surf, (5, 5, 10), term_rect)
        pygame.draw.rect(surf, theme.DIVIDER, term_rect, 1)
        
        line_h = self.font_size + 2
        max_lines = term_rect.height // line_h
        
        visible_lines = list(self.history)[-max_lines:]
        for i, line in enumerate(visible_lines):
            # Very simple wrapping or truncation
            if len(line) > 100: line = line[:97] + "..."
            txt = self.font.render(line, True, (220, 220, 220))
            surf.blit(txt, (term_rect.x + 10, term_rect.y + 10 + i * line_h))
