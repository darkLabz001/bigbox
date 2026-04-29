"""Ragnar — Automated AI-driven pentesting auditor with high-fidelity HUD."""
from __future__ import annotations

import os
import re
import math
import signal
import subprocess
import threading
import pty
import select
import time
import random
import json
from collections import deque
from typing import TYPE_CHECKING, List, Optional, Dict

import pygame

from bigbox import theme, hardware
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

RAGNAR_DIR = "/opt/ragnar"
RAGNAR_EXEC = "/opt/bigbox/.venv/bin/python3"
GAMIFICATION_PATH = "/opt/ragnar/data/gamification.json"

PHASE_LANDING = "landing"
PHASE_CONFIG = "config"
PHASE_RUNNING = "running"

class RagnarView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.history = deque(maxlen=400)
        self.status_msg = "CORE_IDLE"
        
        # Gamification
        self.points = 0
        self.level = 1
        self.stats = {"creds": 0, "vulns": 0, "zombies": 0}
        
        # UI dimensions
        self.font = pygame.font.Font(None, 16)
        self.f_title = pygame.font.Font(None, 36)
        self.f_med = pygame.font.Font(None, 24)
        self.f_tiny = pygame.font.Font(None, 14)
        
        # Attack Options
        self.attacks = {
            "SSH_BRUTE": True,
            "SMB_STEAL": True,
            "SQL_INJECT": True,
            "WIFI_SNITCH": True,
            "BLE_PROBE": True
        }
        self.cursor = 0
        self.scroll_idx = 0
        
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None
        
        # Aesthetics
        self._grid_surf = self._create_grid_bg()
        self._nodes = self._generate_neural_nodes()
        
        self._load_gamification()

    def _create_grid_bg(self) -> pygame.Surface:
        s = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H))
        s.fill((5, 15, 5))
        for x in range(0, theme.SCREEN_W, 40):
            pygame.draw.line(s, (10, 30, 10), (x, 0), (x, theme.SCREEN_H))
        for y in range(0, theme.SCREEN_H, 40):
            pygame.draw.line(s, (10, 30, 10), (0, y), (theme.SCREEN_W, y))
        return s

    def _generate_neural_nodes(self):
        nodes = []
        for _ in range(12):
            nodes.append({
                "pos": [random.randint(520, 780), random.randint(100, 400)],
                "vel": [random.uniform(-0.4, 0.4), random.uniform(-0.4, 0.4)],
                "size": random.randint(2, 4)
            })
        return nodes

    def _load_gamification(self):
        if os.path.exists(GAMIFICATION_PATH):
            try:
                with open(GAMIFICATION_PATH, "r") as f:
                    data = json.load(f)
                    self.points = data.get("total_points", 0)
                    self.level = data.get("level", 1)
                    lc = data.get("lifetime_counts", {})
                    self.stats["creds"] = lc.get("crednbr", 0)
                    self.stats["vulns"] = lc.get("vulnnbr", 0)
                    self.stats["zombies"] = lc.get("zombiesnbr", 0)
            except: pass

    def _start_ragnar(self):
        if not os.path.exists(os.path.join(RAGNAR_DIR, "Ragnar.py")):
            self.status_msg = "ERROR: CORE_MISSING"
            return

        self.phase = PHASE_RUNNING
        self.history.clear()
        self.history.append("[SYSTEM] BOOTING NEURAL_LINK...")
        
        # Build command with only enabled attacks? 
        # For now we run full Ragnar but we can add filter logic later
        cmd = ["sudo", RAGNAR_EXEC, "Ragnar.py"]
        
        self.master_fd, self.slave_fd = pty.openpty()
        try:
            self.process = subprocess.Popen(
                cmd, cwd=RAGNAR_DIR, preexec_fn=os.setsid,
                stdin=self.slave_fd, stdout=self.slave_fd, stderr=self.slave_fd,
                env=os.environ
            )
            self._stop_event.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            self.status_msg = "NEURAL_LINK_ESTABLISHED"
        except Exception as e:
            self.status_msg = f"LINK_FAIL: {e}"

    def _read_output(self):
        while not self._stop_event.is_set() and self.master_fd:
            r, w, e = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 1024).decode("utf-8", "replace")
                    if data:
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        line_h = self.font.get_linesize()
                        max_lines = (theme.SCREEN_H - 140) // line_h
                        was_at_bottom = self.scroll_idx >= len(self.history) - max_lines
                        for line in clean_data.splitlines():
                            if line.strip():
                                self.history.append(line)
                                if "points" in line.lower() or "finding" in line.lower():
                                    self._load_gamification()
                        if was_at_bottom:
                            self.scroll_idx = max(0, len(self.history) - max_lines)
                except OSError: break

    def _send_input(self, text: str):
        if self.master_fd and text:
            os.write(self.master_fd, (text + "\n").encode())

    def _cleanup(self):
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                time.sleep(1)
            except: pass
        if self.master_fd: os.close(self.master_fd)
        if self.slave_fd: os.close(self.slave_fd)
        self.master_fd = self.slave_fd = self.process = None

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            if self.phase == PHASE_RUNNING:
                self._cleanup()
                self.phase = PHASE_LANDING
                self.status_msg = "LINK_DROPPED"
            elif self.phase == PHASE_CONFIG: self.phase = PHASE_LANDING
            else: self.dismissed = True
            return
            
        if self.phase == PHASE_LANDING:
            if ev.button is Button.A: self._start_ragnar()
            elif ev.button is Button.X: self.phase = PHASE_CONFIG
        
        elif self.phase == PHASE_CONFIG:
            keys = list(self.attacks.keys())
            if ev.button is Button.UP: self.cursor = (self.cursor - 1) % len(keys)
            elif ev.button is Button.DOWN: self.cursor = (self.cursor + 1) % len(keys)
            elif ev.button is Button.A: self.attacks[keys[self.cursor]] = not self.attacks[keys[self.cursor]]
            elif ev.button is Button.START: self.phase = PHASE_LANDING

        elif self.phase == PHASE_RUNNING:
            line_h = self.font.get_linesize()
            max_lines = (theme.SCREEN_H - 140) // line_h
            if ev.button in (Button.A, Button.RR):
                ctx.get_input("ORCHESTRATOR COMMAND", self._on_terminal_input)
            elif ev.button is Button.UP: self.scroll_idx = max(0, self.scroll_idx - 1)
            elif ev.button is Button.DOWN: self.scroll_idx = min(self.scroll_idx + 1, max(0, len(self.history) - max_lines))
            elif ev.button is Button.Y: self.history.clear(); self.scroll_idx = 0

    def _on_terminal_input(self, text: str | None):
        if text is not None: self._send_input(text)

    def render(self, surf: pygame.Surface) -> None:
        surf.blit(self._grid_surf, (0, 0))
        head_h = 80
        pygame.draw.rect(surf, (5, 20, 5), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, (0, 255, 100), (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        # Stylized Header & Gamification Bar
        title = self.f_title.render("RAGNAR // NEURAL_HUD", True, (0, 255, 100))
        surf.blit(title, (theme.PADDING, 10))
        
        # XP Bar (Level / Total Points)
        xp_x, xp_y = 400, 15
        pygame.draw.rect(surf, (10, 40, 10), (xp_x, xp_y, 350, 25), border_radius=4)
        progress = (self.points % 1000) / 1000.0
        pygame.draw.rect(surf, (0, 200, 80), (xp_x + 2, xp_y + 2, int(346 * progress), 21), border_radius=2)
        surf.blit(self.f_tiny.render(f"LVL {self.level} // COINS: {self.points}", True, theme.FG), (xp_x + 10, xp_y + 6))
        
        # Mini Stats
        stats_str = f"VULNS: {self.stats['vulns']}  |  CREDS: {self.stats['creds']}  |  ZOMBIES: {self.stats['zombies']}"
        surf.blit(self.f_tiny.render(stats_str, True, (0, 255, 100)), (xp_x, xp_y + 35))

        if self.phase == PHASE_LANDING: self._render_landing(surf, head_h)
        elif self.phase == PHASE_CONFIG: self._render_config(surf, head_h)
        else: self._render_hud(surf, head_h)

        # Footer
        foot_h = 30
        pygame.draw.rect(surf, (2, 10, 2), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, (0, 100, 50), (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        surf.blit(self.f_med.render(f"LINK: {self.status_msg}", True, (0, 255, 100)), (10, theme.SCREEN_H - 25))
        h_surf = self.f_med.render("A: ACTION  X: ATTACKS  B: BACK", True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 25))

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        y = head_h + 30
        box = pygame.Rect(theme.SCREEN_W // 2 - 280, y, 560, 240)
        pygame.draw.rect(surf, (10, 30, 10), box, border_radius=12)
        pygame.draw.rect(surf, (0, 255, 100), box, 1, border_radius=12)
        
        lines = [
            "RAGNAR AI: AUTONOMOUS AUDITING CORE",
            "REASONING: MULTI-AGENT ORCHESTRATION",
            f"VULNERABILITIES ARCHIVED: {self.stats['vulns']}",
            "----------------------------------------",
            f"STATUS: {self.status_msg}",
            "",
            "> PRESS A TO INITIATE NEURAL LINK"
        ]
        for i, ln in enumerate(lines):
            col = (0, 255, 100) if ">" in ln else theme.FG
            surf.blit(self.f_med.render(ln, True, col), (box.x + 40, box.y + 40 + i * 30))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        y = head_h + 20
        surf.blit(self.f_med.render("SELECT ATTACK PROTOCOLS:", True, (0, 255, 100)), (50, y))
        for i, (name, val) in enumerate(self.attacks.items()):
            sel = i == self.cursor
            rect = pygame.Rect(50, y + 40 + i*40, 400, 35)
            if sel: pygame.draw.rect(surf, (20, 50, 20), rect, border_radius=4)
            surf.blit(self.f_med.render(f"[{'X' if val else ' '}] {name}", True, theme.FG if not sel else (0, 255, 100)), (70, rect.y + 8))

    def _render_hud(self, surf: pygame.Surface, head_h: int):
        term_rect = pygame.Rect(10, head_h + 10, 520, theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (0, 10, 0, 180), term_rect, border_radius=4)
        pygame.draw.rect(surf, (0, 100, 0), term_rect, 1, border_radius=4)
        
        line_h = self.font.get_linesize()
        max_lines = term_rect.height // line_h
        start = max(0, min(self.scroll_idx, len(self.history) - max_lines))
        visible = list(self.history)[start : start + max_lines]
        for i, line in enumerate(visible):
            color = (0, 200, 50)
            if "fail" in line.lower(): color = theme.ERR
            if "success" in line.lower() or "ok" in line.lower(): color = (100, 255, 100)
            surf.blit(self.font.render(line[:90], True, color), (term_rect.x + 10, term_rect.y + 5 + i * line_h))

        # AI Map / Animation
        for n in self._nodes:
            n["pos"][0] += n["vel"][0]; n["pos"][1] += n["vel"][1]
            if n["pos"][0] < 540 or n["pos"][0] > 780: n["vel"][0] *= -1
            if n["pos"][1] < head_h or n["pos"][1] > 400: n["vel"][1] *= -1
            pygame.draw.circle(surf, (0, 150, 80), (int(n["pos"][0]), int(n["pos"][1])), n["size"])
        surf.blit(self.f_tiny.render("NEURAL_STATE: ANALYZING", True, (0, 150, 80)), (550, 420))
