"""Ragnar — Automated AI-driven pentesting auditor."""
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

RAGNAR_DIR = "/opt/ragnar"
RAGNAR_EXEC = "/opt/bigbox/.venv/bin/python3" # Use system venv or dedicated one?
# Based on installer, we'll assume it's installed and we run it with our venv.

PHASE_LANDING = "landing"
PHASE_RUNNING = "running"

class RagnarView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.history = deque(maxlen=400)
        self.status_msg = "AI-DRIVEN AUDITOR"
        
        # UI dimensions
        self.font_size = 16
        self.font = pygame.font.Font(None, self.font_size)
        self.f_title = pygame.font.Font(None, 42)
        self.f_med = pygame.font.Font(None, 24)
        
        self.scroll_idx = 0
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None
        
        self._grid_surf = self._create_grid_bg()

    def _create_grid_bg(self) -> pygame.Surface:
        s = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H))
        s.fill(theme.BG)
        for x in range(0, theme.SCREEN_W, 40):
            pygame.draw.line(s, (15, 20, 15), (x, 0), (x, theme.SCREEN_H))
        for y in range(0, theme.SCREEN_H, 40):
            pygame.draw.line(s, (15, 20, 15), (0, y), (theme.SCREEN_W, y))
        return s

    def _start_ragnar(self):
        if not os.path.exists(os.path.join(RAGNAR_DIR, "Ragnar.py")):
            self.status_msg = "ERROR: Ragnar not found in /opt/ragnar"
            return

        self.phase = PHASE_RUNNING
        self.history.clear()
        self.history.append("[SYSTEM] INITIATING RAGNAR CORE...")
        
        cmd = ["sudo", RAGNAR_EXEC, "Ragnar.py"]
        
        self.master_fd, self.slave_fd = pty.openpty()
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=RAGNAR_DIR,
                preexec_fn=os.setsid,
                stdin=self.slave_fd,
                stdout=self.slave_fd,
                stderr=self.slave_fd,
                env=os.environ
            )
            self._stop_event.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            self.status_msg = "AUDITOR ACTIVE"
        except Exception as e:
            self.status_msg = f"LAUNCH_FAIL: {e}"

    def _read_output(self):
        while not self._stop_event.is_set() and self.master_fd:
            r, w, e = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 1024).decode("utf-8", "replace")
                    if data:
                        import re
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        line_h = self.font.get_linesize()
                        max_lines = (theme.SCREEN_H - 120) // line_h
                        was_at_bottom = self.scroll_idx >= len(self.history) - max_lines

                        for line in clean_data.splitlines():
                            if line.strip():
                                self.history.append(line)
                        
                        if was_at_bottom:
                            self.scroll_idx = max(0, len(self.history) - max_lines)
                except OSError:
                    break

    def _send_input(self, text: str):
        if self.master_fd and text:
            os.write(self.master_fd, (text + "\n").encode())

    def _cleanup(self):
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                time.sleep(1)
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

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            if self.phase == PHASE_RUNNING:
                self._cleanup()
                self.phase = PHASE_LANDING
                self.status_msg = "AUDIT ABORTED"
            else:
                self.dismissed = True
            return
            
        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                self._start_ragnar()
        elif self.phase == PHASE_RUNNING:
            line_h = self.font.get_linesize()
            max_lines = (theme.SCREEN_H - 120) // line_h
            
            if ev.button in (Button.A, Button.RR):
                ctx.get_input("RAGNAR INPUT", self._on_terminal_input)
            elif ev.button is Button.UP:
                self.scroll_idx = max(0, self.scroll_idx - 1)
            elif ev.button is Button.DOWN:
                self.scroll_idx = min(self.scroll_idx + 1, max(0, len(self.history) - max_lines))
            elif ev.button is Button.Y:
                self.history.clear()
                self.scroll_idx = 0

    def _on_terminal_input(self, text: str | None):
        if text is not None: self._send_input(text)

    def render(self, surf: pygame.Surface) -> None:
        surf.blit(self._grid_surf, (0, 0))
        head_h = 60
        pygame.draw.rect(surf, (10, 20, 10), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, (0, 255, 0), (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        title = self.f_title.render("RAGNAR :: AI_PENTESTER", True, (0, 255, 0))
        surf.blit(title, (theme.PADDING, 10))

        if self.phase == PHASE_LANDING:
            self._render_landing(surf, head_h)
        else:
            self._render_terminal(surf, head_h)

        # Footer
        foot_h = 30
        pygame.draw.rect(surf, (5, 10, 5), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, (0, 100, 0), (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        surf.blit(self.f_med.render(self.status_msg, True, (0, 255, 0)), (10, theme.SCREEN_H - 25))
        
        hint = "A: INITIATE  B: BACK" if self.phase == PHASE_LANDING else "A: INPUT  UP/DN: SCROLL  B: STOP"
        h_surf = self.f_med.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 25))

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        y = head_h + 40
        box = pygame.Rect(theme.SCREEN_W // 2 - 250, y, 500, 200)
        pygame.draw.rect(surf, (15, 25, 15), box, border_radius=8)
        pygame.draw.rect(surf, (0, 255, 0), box, 1, border_radius=8)
        
        lines = [
            "RAGNAR: Automated AI Auditing Framework",
            "Targets: Local Network, Web, and Cloud",
            "Logic: GPT-4o / Local AI driven reasoning",
            "",
            "READY FOR DEPLOYMENT",
        ]
        for i, ln in enumerate(lines):
            col = (0, 255, 0) if "READY" in ln else theme.FG
            surf.blit(self.f_med.render(ln, True, col), (box.x + 30, box.y + 40 + i * 35))

    def _render_terminal(self, surf: pygame.Surface, head_h: int):
        term_rect = pygame.Rect(10, head_h + 10, theme.SCREEN_W - 20, theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (0, 5, 0, 220), term_rect)
        pygame.draw.rect(surf, (0, 150, 0), term_rect, 1)
        
        line_h = self.font.get_linesize()
        max_lines = term_rect.height // line_h
        total = len(self.history)
        
        start = max(0, min(self.scroll_idx, total - max_lines))
        visible = list(self.history)[start : start + max_lines]
        
        for i, line in enumerate(visible):
            color = (0, 200, 0)
            if "FAIL" in line or "ERROR" in line: color = theme.ERR
            if "SUCCESS" in line or "OK" in line: color = (100, 255, 100)
            surf.blit(self.font.render(line[:120], True, color), (term_rect.x + 10, term_rect.y + 5 + i * line_h))
