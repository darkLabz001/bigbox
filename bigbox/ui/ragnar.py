"""Ragnar — Automated AI-driven pentesting auditor with high-fidelity HUD."""
from __future__ import annotations

import os
import math
import signal
import subprocess
import threading
import pty
import select
import time
import random
from collections import deque
from typing import TYPE_CHECKING, List, Optional

import pygame

from bigbox import theme, hardware
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

RAGNAR_DIR = "/opt/ragnar"
RAGNAR_EXEC = "/opt/bigbox/.venv/bin/python3"

PHASE_LANDING = "landing"
PHASE_RUNNING = "running"

class RagnarView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.history = deque(maxlen=400)
        self.status_msg = "CORE_IDLE"
        
        # UI dimensions
        self.font_size = 16
        self.font = pygame.font.Font(None, self.font_size)
        self.f_title = pygame.font.Font(None, 42)
        self.f_med = pygame.font.Font(None, 24)
        self.f_tiny = pygame.font.Font(None, 14)
        
        self.scroll_idx = 0
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None
        
        # Aesthetics
        self._grid_surf = self._create_grid_bg()
        self._nodes = self._generate_neural_nodes()
        self._logic_ticks = 0

    def _create_grid_bg(self) -> pygame.Surface:
        s = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H))
        s.fill((5, 15, 5)) # Deep green tint
        for x in range(0, theme.SCREEN_W, 40):
            pygame.draw.line(s, (10, 30, 10), (x, 0), (x, theme.SCREEN_H))
        for y in range(0, theme.SCREEN_H, 40):
            pygame.draw.line(s, (10, 30, 10), (0, y), (theme.SCREEN_W, y))
        return s

    def _generate_neural_nodes(self):
        nodes = []
        for _ in range(15):
            nodes.append({
                "pos": [random.randint(520, 780), random.randint(60, 420)],
                "vel": [random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5)],
                "size": random.randint(2, 4)
            })
        return nodes

    def _start_ragnar(self):
        if not os.path.exists(os.path.join(RAGNAR_DIR, "Ragnar.py")):
            self.status_msg = "ERROR: CORE_FILES_MISSING"
            return

        self.phase = PHASE_RUNNING
        self.history.clear()
        self.history.append("[SYSTEM] UPLINKING TO AI ORCHESTRATOR...")
        
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
            self.status_msg = "NEURAL_LINK_ACTIVE"
        except Exception as e:
            self.status_msg = f"LINK_FAILURE: {e}"

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
                self.status_msg = "LINK_TERMINATED"
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
                ctx.get_input("AI COMMAND", self._on_terminal_input)
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
        pygame.draw.rect(surf, (5, 20, 5), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, (0, 255, 100), (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        # Glitchy Title
        title_text = "RAGNAR // NEURAL_AUDITOR"
        if random.random() > 0.98: title_text = "R4GN4R // XXXXXXXXXXXXX"
        title = self.f_title.render(title_text, True, (0, 255, 100))
        surf.blit(title, (theme.PADDING, 12))

        if self.phase == PHASE_LANDING:
            self._render_landing(surf, head_h)
        else:
            self._render_hud(surf, head_h)

        # Status Bar
        foot_h = 30
        pygame.draw.rect(surf, (2, 10, 2), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, (0, 100, 50), (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        
        # Blinking status
        st_color = (0, 255, 100) if (int(time.time()*2)%2) else (0, 150, 50)
        surf.blit(self.f_med.render(f"LINK_STATUS: {self.status_msg}", True, st_color), (10, theme.SCREEN_H - 25))
        
        hint = "A: INITIATE  B: BACK" if self.phase == PHASE_LANDING else "A: CMD_INPUT  UP/DN: SCROLL  B: STOP"
        h_surf = self.f_med.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 25))

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        y = head_h + 50
        box = pygame.Rect(theme.SCREEN_W // 2 - 280, y, 560, 220)
        pygame.draw.rect(surf, (10, 30, 10), box, border_radius=12)
        pygame.draw.rect(surf, (0, 255, 100), box, 1, border_radius=12)
        
        lines = [
            "RAGNAR CORE v2.0 - AUTONOMOUS AI AGENT",
            "MODE: ADVANCED PENTESTING & EXPLOITATION",
            "LOGIC: GPT-4o QUANTIZED REASONING",
            "----------------------------------------",
            "READY TO BREACH LOCAL INFRASTRUCTURE",
        ]
        for i, ln in enumerate(lines):
            col = (0, 255, 100) if "READY" in ln else theme.FG
            surf.blit(self.f_med.render(ln, True, col), (box.x + 40, box.y + 40 + i * 32))

    def _render_hud(self, surf: pygame.Surface, head_h: int):
        # 1. Main Terminal Window (Left)
        term_w = 500
        term_rect = pygame.Rect(10, head_h + 10, term_w, theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (0, 10, 0, 200), term_rect, border_radius=4)
        pygame.draw.rect(surf, (0, 100, 0), term_rect, 1, border_radius=4)
        
        line_h = self.font.get_linesize()
        max_lines = term_rect.height // line_h
        total = len(self.history)
        start = max(0, min(self.scroll_idx, total - max_lines))
        visible = list(self.history)[start : start + max_lines]
        
        for i, line in enumerate(visible):
            color = (0, 200, 50)
            if "fail" in line.lower(): color = theme.ERR
            if "success" in line.lower() or "ok" in line.lower(): color = (100, 255, 100)
            surf.blit(self.font.render(line[:85], True, color), (term_rect.x + 10, term_rect.y + 5 + i * line_h))

        # 2. AI Intelligence Panel (Right)
        pane_x = term_w + 25
        pane_y = head_h + 10
        surf.blit(self.f_med.render("NEURAL_MAP", True, (0, 255, 100)), (pane_x, pane_y))
        
        # Animate neural nodes
        for n in self._nodes:
            n["pos"][0] += n["vel"][0]
            n["pos"][1] += n["vel"][1]
            if n["pos"][0] < 520 or n["pos"][0] > 780: n["vel"][0] *= -1
            if n["pos"][1] < 60 or n["pos"][1] > 420: n["vel"][1] *= -1
            pygame.draw.circle(surf, (0, 100, 0), (int(n["pos"][0]), int(n["pos"][1])), n["size"])
            # Draw faint lines between nearby nodes
            for other in self._nodes:
                dist = math.hypot(n["pos"][0] - other["pos"][0], n["pos"][1] - other["pos"][1])
                if dist < 60:
                    pygame.draw.line(surf, (0, 40, 0), n["pos"], other["pos"], 1)

        # 3. AI Status Stats
        stat_y = pane_y + 150
        stats = [
            ("COG_LOAD:", f"{random.randint(12, 45)}%"),
            ("TOKEN_HZ:", f"{random.randint(100, 900)}ms"),
            ("LINK_Q:", "STABLE"),
            ("TARGETS:", f"{len(self.history)//10}"),
        ]
        for i, (lbl, val) in enumerate(stats):
            surf.blit(self.f_tiny.render(lbl, True, (0, 150, 50)), (pane_x, stat_y + i*25))
            surf.blit(self.f_med.render(val, True, theme.FG), (pane_x + 90, stat_y + i*25 - 4))
        
        # Scanline overlay for whole HUD
        for y in range(pane_y, theme.SCREEN_H - 50, 4):
            pygame.draw.line(surf, (0, 20, 0, 50), (pane_x, y), (theme.SCREEN_W - 10, y))
