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
import csv
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional, Dict

import pygame

from bigbox import theme, hardware, qr
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

RAGNAR_DIR = "/opt/ragnar"
RAGNAR_EXEC = "/opt/bigbox/.venv/bin/python3"
GAMIFICATION_PATH = "/opt/ragnar/data/gamification.json"
NETKB_PATH = "/opt/ragnar/data/netkb.csv"
ENV_PATH = "/opt/ragnar/.env"

PHASE_LANDING = "landing"
PHASE_HUD = "hud"
PHASE_TARGETS = "targets"
PHASE_DETAILS = "details"
PHASE_CONFIG = "config"
PHASE_QR = "qr"

@dataclass
class RagnarTarget:
    ip: str = ""
    mac: str = ""
    hostname: str = ""
    vendor: str = ""
    os: str = ""
    services: str = ""
    vulns: str = ""
    last_seen: str = ""

class RagnarView:
    def __init__(self, initial_phase: str = PHASE_LANDING) -> None:
        self.dismissed = False
        self.phase = initial_phase
        self.history = deque(maxlen=400)
        self.status_msg = "CORE_IDLE" if initial_phase == PHASE_LANDING else "LOG_VIEW_MODE"
        
        # Gamification
        self.points = 0
        self.level = 1
        self.stats = {"creds": 0, "vulns": 0, "zombies": 0}
        
        # Targets
        self.targets: list[Dict] = []
        self.target_cursor = 0
        
        # UI dimensions
        self.font = pygame.font.Font(None, 16)
        self.f_title = pygame.font.Font(None, 36)
        self.f_med = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 18)
        self.f_tiny = pygame.font.Font(None, 14)
        
        # Config & Env
        self.openai_key = ""
        self.attacks = {
            "AI_REASONING": True,
            "SSH_BRUTE": True,
            "SMB_STEAL": True,
            "SQL_INJECT": True,
            "WIFI_SNITCH": True,
            "BLE_PROBE": True
        }
        self.config_cursor = 0
        self.config_options = ["EDIT_OPENAI_KEY"] + list(self.attacks.keys())
        
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None
        self._poll_thread = None
        
        # Aesthetics
        self._grid_surf = self._create_grid_bg()
        self._nodes = self._generate_neural_nodes()
        self._radar_angle = 0
        
        self._load_env()
        self._load_gamification()
        self._load_targets()

    def _load_env(self):
        if os.path.exists(ENV_PATH):
            try:
                with open(ENV_PATH, "r") as f:
                    for line in f:
                        if line.startswith("OPENAI_API_KEY="):
                            self.openai_key = line.split("=", 1)[1].strip()
            except: pass

    def _save_env(self):
        try:
            lines = []
            if os.path.exists(ENV_PATH):
                with open(ENV_PATH, "r") as f:
                    lines = f.readlines()
            
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("OPENAI_API_KEY="):
                    new_lines.append(f"OPENAI_API_KEY={self.openai_key}\n")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"OPENAI_API_KEY={self.openai_key}\n")
            
            with open(ENV_PATH, "w") as f:
                f.writelines(new_lines)
            self.status_msg = "ENV_UPDATED"
        except Exception as e:
            self.status_msg = f"SAVE_FAIL: {e}"

    def _create_grid_bg(self) -> pygame.Surface:
        s = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H))
        s.fill((2, 10, 2))
        for x in range(0, theme.SCREEN_W, 40):
            pygame.draw.line(s, (5, 20, 5), (x, 0), (x, theme.SCREEN_H))
        for y in range(0, theme.SCREEN_H, 40):
            pygame.draw.line(s, (5, 20, 5), (0, y), (theme.SCREEN_W, y))
        return s

    def _generate_neural_nodes(self):
        nodes = []
        # Center node (Darkbox)
        nodes.append({"pos": [660, 250], "type": "CORE", "label": "DARKBOX", "size": 8})
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

    def _load_targets(self):
        if not os.path.exists(NETKB_PATH):
            return
        try:
            new_targets = []
            with open(NETKB_PATH, "r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    new_targets.append(row)
            
            if len(new_targets) > len(self.targets):
                self._play_beep()
            
            self.targets = new_targets
            self._update_neural_nodes()
        except: pass

    def _update_neural_nodes(self):
        core = self._nodes[0]
        self._nodes = [core]
        
        # Add up to 12 zombies around the core
        for i, t in enumerate(self.targets[:12]):
            angle = (i / min(len(self.targets), 12)) * 2 * math.pi
            dist = random.randint(80, 140)
            x = core["pos"][0] + math.cos(angle) * dist
            y = core["pos"][1] + math.sin(angle) * dist
            self._nodes.append({
                "pos": [x, y],
                "type": "ZOMBIE",
                "label": t.get("ip", "??"),
                "size": 4,
                "vulns": int(t.get("vulns", 0) or 0) > 0
            })

    def _play_beep(self):
        try:
            if not pygame.mixer.get_init(): pygame.mixer.init()
            import array
            sample_rate, freq, duration = 44100, 1000, 0.1
            n_samples = int(sample_rate * duration)
            buf = array.array('h', [int(16384 * math.sin(2 * math.pi * freq * i / sample_rate)) for i in range(n_samples)])
            pygame.mixer.Sound(buffer=buf).play()
        except: pass

    def _start_ragnar(self):
        if not os.path.exists(os.path.join(RAGNAR_DIR, "Ragnar.py")):
            self.status_msg = "ERROR: CORE_MISSING"
            return

        self.phase = PHASE_HUD
        self.history.clear()
        self.history.append("[SYSTEM] INITIATING NEURAL_LINK...")
        
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
            
            self._poll_thread = threading.Thread(target=self._poll_data, daemon=True)
            self._poll_thread.start()
            
            self.status_msg = "NEURAL_LINK_ACTIVE"
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
                        for line in clean_data.splitlines():
                            if line.strip():
                                self.history.append(line)
                                if "points" in line.lower() or "finding" in line.lower():
                                    self._load_gamification()
                except OSError: break

    def _poll_data(self):
        while not self._stop_event.is_set():
            self._load_gamification()
            self._load_targets()
            time.sleep(5)

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
            if self.phase == PHASE_QR: self.phase = PHASE_HUD
            elif self.phase == PHASE_DETAILS: self.phase = PHASE_TARGETS
            elif self.phase in (PHASE_HUD, PHASE_TARGETS, PHASE_CONFIG):
                self._cleanup()
                self.phase = PHASE_LANDING
                self.status_msg = "LINK_DROPPED"
            else: self.dismissed = True
            return
            
        if self.phase == PHASE_LANDING:
            if ev.button is Button.A: self._start_ragnar()
            elif ev.button is Button.X: 
                self.status_msg = "WEB_UI_ACCESS"
                self.phase = PHASE_QR
        
        elif self.phase == PHASE_HUD:
            if ev.button is Button.X: self.phase = PHASE_TARGETS
            elif ev.button is Button.Y: self.phase = PHASE_CONFIG
            elif ev.button in (Button.A, Button.START):
                ctx.get_input("ORCHESTRATOR COMMAND", self._on_terminal_input)
            elif ev.button is Button.UP: self.history.rotate(1)
            elif ev.button is Button.DOWN: self.history.rotate(-1)

        elif self.phase == PHASE_TARGETS:
            if ev.button is Button.X: self.phase = PHASE_CONFIG
            elif ev.button is Button.Y: self.phase = PHASE_HUD
            if not self.targets: return
            if ev.button is Button.UP: self.target_cursor = (self.target_cursor - 1) % len(self.targets)
            elif ev.button is Button.DOWN: self.target_cursor = (self.target_cursor + 1) % len(self.targets)
            elif ev.button is Button.A: self.phase = PHASE_DETAILS
            
        elif self.phase == PHASE_CONFIG:
            if ev.button is Button.X: self.phase = PHASE_HUD
            elif ev.button is Button.Y: self.phase = PHASE_TARGETS
            if ev.button is Button.UP: self.config_cursor = (self.config_cursor - 1) % len(self.config_options)
            elif ev.button is Button.DOWN: self.config_cursor = (self.config_cursor + 1) % len(self.config_options)
            elif ev.button is Button.A:
                opt = self.config_options[self.config_cursor]
                if opt == "EDIT_OPENAI_KEY":
                    ctx.get_input("OPENAI API KEY", self._on_openai_input, self.openai_key)
                else:
                    self.attacks[opt] = not self.attacks[opt]

    def _on_openai_input(self, text: str | None):
        if text is not None:
            self.openai_key = text
            self._save_env()

    def _on_terminal_input(self, text: str | None):
        if text is not None: self._send_input(text)

    def render(self, surf: pygame.Surface) -> None:
        surf.blit(self._grid_surf, (0, 0))
        head_h = 80
        pygame.draw.rect(surf, (5, 20, 5), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, (0, 255, 100), (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        # Stylized Header
        title = self.f_title.render("RAGNAR // NEURAL_HUD", True, (0, 255, 100))
        surf.blit(title, (theme.PADDING, 10))
        
        # XP Bar
        xp_x, xp_y = 450, 15
        pygame.draw.rect(surf, (10, 40, 10), (xp_x, xp_y, 300, 25), border_radius=4)
        progress = (self.points % 1000) / 1000.0
        pygame.draw.rect(surf, (0, 200, 80), (xp_x + 2, xp_y + 2, int(296 * progress), 21), border_radius=2)
        surf.blit(self.f_tiny.render(f"LVL {self.level} // COINS: {self.points}", True, theme.FG), (xp_x + 10, xp_y + 6))
        
        # Stats
        stats_str = f"VULNS: {self.stats['vulns']} | CREDS: {self.stats['creds']} | ZOMBIES: {self.stats['zombies']}"
        surf.blit(self.f_tiny.render(stats_str, True, (0, 255, 100)), (xp_x, xp_y + 35))

        if self.phase == PHASE_LANDING: self._render_landing(surf, head_h)
        elif self.phase == PHASE_HUD: self._render_hud(surf, head_h)
        elif self.phase == PHASE_TARGETS: self._render_targets(surf, head_h)
        elif self.phase == PHASE_DETAILS: self._render_details(surf, head_h)
        elif self.phase == PHASE_CONFIG: self._render_config(surf, head_h)
        elif self.phase == PHASE_QR: self._render_qr(surf, head_h)

        # Footer
        foot_h = 30
        pygame.draw.rect(surf, (2, 10, 2), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, (0, 100, 50), (0, theme.SCREEN_H - foot_h), (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        surf.blit(self.f_med.render(f"STATUS: {self.status_msg}", True, (0, 255, 100)), (10, theme.SCREEN_H - 25))
        
        hints = {
            PHASE_LANDING: "A: START  X: WEB_UI  B: EXIT",
            PHASE_HUD: "X: TARGETS  Y: CONFIG  A: CMD  B: STOP",
            PHASE_TARGETS: "X: CONFIG  Y: HUD  A: DETAILS  B: STOP",
            PHASE_DETAILS: "B: BACK",
            PHASE_CONFIG: "X: HUD  Y: TARGETS  A: TOGGLE  B: STOP",
            PHASE_QR: "B: BACK"
        }
        h_surf = self.f_med.render(hints.get(self.phase, "B: BACK"), True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 10, theme.SCREEN_H - 25))

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        y = head_h + 50
        box = pygame.Rect(theme.SCREEN_W // 2 - 280, y, 560, 220)
        pygame.draw.rect(surf, (5, 25, 5), box, border_radius=12)
        pygame.draw.rect(surf, (0, 255, 100), box, 1, border_radius=12)
        
        lines = [
            "RAGNAR: AUTONOMOUS NEURAL AUDITOR",
            "REASONING: MULTI-AGENT ORCHESTRATION",
            "------------------------------------",
            "PRESS A TO INITIATE LINK",
            "PRESS X TO GENERATE WEB ACCESS QR"
        ]
        for i, ln in enumerate(lines):
            col = (0, 255, 100) if "PRESS" in ln else theme.FG
            surf.blit(self.f_med.render(ln, True, col), (box.x + 40, box.y + 40 + i * 35))

    def _render_hud(self, surf: pygame.Surface, head_h: int):
        # Log View
        log_rect = pygame.Rect(10, head_h + 10, 520, theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (0, 15, 0), log_rect, border_radius=4)
        pygame.draw.rect(surf, (0, 100, 0), log_rect, 1, border_radius=4)
        
        line_h = self.font.get_linesize()
        max_lines = log_rect.height // line_h - 2
        visible = list(self.history)[-max_lines:]
        for i, line in enumerate(visible):
            surf.blit(self.font.render(line[:90], True, (0, 200, 50)), (log_rect.x + 10, log_rect.y + 10 + i * line_h))

        # Neural Map Area
        self._render_neural_map(surf, head_h)
        self._render_radar(surf, head_h)

    def _render_neural_map(self, surf: pygame.Surface, head_h: int):
        map_rect = pygame.Rect(540, head_h + 10, 250, 250)
        pygame.draw.rect(surf, (0, 15, 0), map_rect, border_radius=4)
        pygame.draw.rect(surf, (0, 100, 0), map_rect, 1, border_radius=4)
        
        core = self._nodes[0]
        for node in self._nodes[1:]:
            pygame.draw.line(surf, (0, 50, 0), core["pos"], node["pos"], 1)
            col = (255, 50, 50) if node.get("vulns") else (0, 150, 80)
            pygame.draw.circle(surf, col, (int(node["pos"][0]), int(node["pos"][1])), node["size"])
            label = self.f_tiny.render(node["label"], True, theme.FG_DIM)
            surf.blit(label, (node["pos"][0] + 5, node["pos"][1] - 5))
            
        pygame.draw.circle(surf, (0, 255, 100), core["pos"], core["size"])
        surf.blit(self.f_tiny.render("CORE", True, (0, 255, 100)), (core["pos"][0] - 15, core["pos"][1] + 10))

    def _render_radar(self, surf: pygame.Surface, head_h: int):
        radar_rect = pygame.Rect(540, head_h + 270, 250, 90)
        pygame.draw.rect(surf, (0, 15, 0), radar_rect, border_radius=4)
        pygame.draw.rect(surf, (0, 100, 0), radar_rect, 1, border_radius=4)
        
        # Radar scan line
        center = (radar_rect.x + 45, radar_rect.y + 45)
        radius = 35
        pygame.draw.circle(surf, (0, 50, 0), center, radius, 1)
        self._radar_angle = (self._radar_angle + 5) % 360
        rad = math.radians(self._radar_angle)
        end = (center[0] + math.cos(rad) * radius, center[1] + math.sin(rad) * radius)
        pygame.draw.line(surf, (0, 255, 100), center, end, 2)
        
        surf.blit(self.f_small.render("SCANNER ACTIVE", True, (0, 255, 100)), (radar_rect.x + 100, radar_rect.y + 15))
        surf.blit(self.f_tiny.render(f"ZOMBIES: {len(self.targets)}", True, theme.FG), (radar_rect.x + 100, radar_rect.y + 40))
        surf.blit(self.f_tiny.render(f"LINK_QLTY: 98%", True, (0, 150, 80)), (radar_rect.x + 100, radar_rect.y + 55))

    def _render_targets(self, surf: pygame.Surface, head_h: int):
        box = pygame.Rect(10, head_h + 10, 780, theme.SCREEN_H - head_h - 50)
        pygame.draw.rect(surf, (0, 15, 0), box, border_radius=4)
        pygame.draw.rect(surf, (0, 100, 0), box, 1, border_radius=4)
        
        if not self.targets:
            surf.blit(self.f_med.render("NO TARGETS IDENTIFIED", True, theme.FG_DIM), (theme.SCREEN_W // 2 - 100, theme.SCREEN_H // 2))
            return

        cols = [("IP", 120), ("HOSTNAME", 180), ("VENDOR", 150), ("VULNS", 60), ("OS", 100)]
        x = box.x + 20
        for name, width in cols:
            surf.blit(self.f_small.render(name, True, (0, 255, 100)), (x, box.y + 15))
            x += width
            
        pygame.draw.line(surf, (0, 50, 0), (box.x + 10, box.y + 35), (box.right - 10, box.y + 35))
        
        row_h = 28
        visible = (box.height - 50) // row_h
        start = max(0, min(self.target_cursor - visible // 2, len(self.targets) - visible))
        
        for i in range(visible):
            idx = start + i
            if idx >= len(self.targets): break
            t = self.targets[idx]
            y = box.y + 45 + i * row_h
            if idx == self.target_cursor:
                pygame.draw.rect(surf, (10, 40, 10), (box.x + 5, y - 2, box.width - 10, row_h), border_radius=4)
            
            tx = box.x + 20
            surf.blit(self.f_small.render(t.get("ip", "??"), True, theme.FG), (tx, y))
            tx += 120
            surf.blit(self.f_small.render(t.get("hostname", "??")[:20], True, theme.FG), (tx, y))
            tx += 180
            surf.blit(self.f_small.render(t.get("vendor", "??")[:18], True, theme.FG), (tx, y))
            tx += 150
            vcol = (255, 50, 50) if int(t.get("vulns", 0) or 0) > 0 else theme.FG_DIM
            surf.blit(self.f_small.render(t.get("vulns", "0"), True, vcol), (tx, y))
            tx += 60
            surf.blit(self.f_small.render(t.get("os", "??")[:12], True, theme.FG), (tx, y))

    def _render_details(self, surf: pygame.Surface, head_h: int):
        if not self.targets or self.target_cursor >= len(self.targets):
            self.phase = PHASE_TARGETS
            return
            
        t = self.targets[self.target_cursor]
        box = pygame.Rect(100, head_h + 30, 600, 300)
        pygame.draw.rect(surf, (5, 25, 5), box, border_radius=12)
        pygame.draw.rect(surf, (0, 255, 100), box, 1, border_radius=12)
        
        title = self.f_med.render(f"ZOMBIE_DETAILS: {t.get('ip', '??')}", True, (0, 255, 100))
        surf.blit(title, (box.x + 30, box.y + 20))
        
        lines = [
            f"HOSTNAME: {t.get('hostname', '??')}",
            f"MAC_ADDR: {t.get('mac', '??')}",
            f"VENDOR:   {t.get('vendor', '??')}",
            f"OS_PROTO: {t.get('os', '??')}",
            f"SERVICES: {t.get('services', '??')}",
            f"VULNS:    {t.get('vulns', '??')}",
            f"LAST_SEEN: {t.get('last_seen', '??')}"
        ]
        for i, ln in enumerate(lines):
            surf.blit(self.f_small.render(ln, True, theme.FG), (box.x + 40, box.y + 70 + i * 25))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        y = head_h + 30
        surf.blit(self.f_med.render("PROTOCOLS & NEURAL_LINK SETTINGS", True, (0, 255, 100)), (50, y))
        for i, opt in enumerate(self.config_options):
            sel = i == self.config_cursor
            rect = pygame.Rect(50, y + 50 + i*40, 450, 35)
            if sel: pygame.draw.rect(surf, (10, 40, 10), rect, border_radius=4)
            pygame.draw.rect(surf, (0, 100, 0), rect, 1, border_radius=4)
            surf.blit(self.f_med.render(f"{opt}", True, theme.FG if not sel else (0, 255, 100)), (70, rect.y + 7))
            
            if opt in self.attacks:
                val = self.attacks[opt]
                status = "ENABLED" if val else "DISABLED"
                scol = (100, 255, 100) if val else theme.FG_DIM
                surf.blit(self.f_med.render(status, True, scol), (rect.right - 100, rect.y + 7))
            elif opt == "EDIT_OPENAI_KEY":
                masked = (self.openai_key[:8] + "...") if self.openai_key else "MISSING"
                surf.blit(self.f_small.render(masked, True, (100, 255, 100) if self.openai_key else theme.ERR), (rect.right - 150, rect.y + 10))

    def _render_qr(self, surf: pygame.Surface, head_h: int):
        ip = qr.lan_ipv4() or "127.0.0.1"
        url = f"http://{ip}:8000"
        
        box_w, box_h = 400, 320
        bx = (theme.SCREEN_W - box_w) // 2
        by = (theme.SCREEN_H - box_h) // 2 + 30
        pygame.draw.rect(surf, (5, 25, 5), (bx, by, box_w, box_h), border_radius=12)
        pygame.draw.rect(surf, (0, 255, 100), (bx, by, box_w, box_h), 2, border_radius=12)
        
        surf.blit(self.f_med.render("RAGNAR WEB_UI ACCESS", True, (0, 255, 100)), (bx + 80, by + 20))
        
        matrix = qr.make_matrix(url)
        if matrix:
            m_size = len(matrix)
            cell = min(180 // m_size, 6)
            qx = bx + (box_w - m_size * cell) // 2
            qy = by + 60
            for r, row in enumerate(matrix):
                for c, val in enumerate(row):
                    if val:
                        pygame.draw.rect(surf, (0, 255, 100), (qx + c*cell, qy + r*cell, cell, cell))
        
        surf.blit(self.f_small.render(url, True, theme.FG), (bx + (box_w - self.f_small.size(url)[0]) // 2, by + box_h - 50))
        surf.blit(self.f_tiny.render("SCAN TO OPEN HUD IN BROWSER", True, theme.FG_DIM), (bx + 90, by + box_h - 30))
