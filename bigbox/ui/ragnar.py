"""Ragnar — Professional Network Audit & Exploitation Framework."""
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
RAGNAR_VENV_EXEC = "/opt/ragnar/venv/bin/python3"
RAGNAR_SYS_EXEC = "/usr/bin/python3"
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
        self.status_msg = "CORE_IDLE" if initial_phase == PHASE_LANDING else "RECOVERY_MODE"
        
        # Gamification
        self.points = 0
        self.level = 1
        self.stats = {"creds": 0, "vulns": 0, "zombies": 0}
        
        # Targets
        self.targets: list[Dict] = []
        self.target_cursor = 0
        
        # UI dimensions
        self.f_main = pygame.font.Font(None, 22)
        self.f_title = pygame.font.Font(None, 34)
        self.f_bold = pygame.font.Font(None, 26)
        self.f_small = pygame.font.Font(None, 18)
        self.f_tiny = pygame.font.Font(None, 14)
        
        # Config & Env
        self.openai_key = ""
        self.attacks = {
            "AUTONOMOUS_MODE": True,
            "AI_ORCHESTRATION": True,
            "SVC_EXPLOITATION": True,
            "CRED_HARVESTING": True,
            "PASSIVE_SNIFFING": True,
            "BLE_DISCOVERY": True
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
        self._frame_count = 0
        self._nodes = self._generate_topology_nodes()
        self._wave_offset = 0
        self._cpu_history = deque([random.uniform(5, 15) for _ in range(50)], maxlen=50)
        
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
            self.status_msg = "ENV_CONFIG_COMMITTED"
        except Exception as e:
            self.status_msg = f"ERR: {e}"

    def _generate_topology_nodes(self):
        nodes = []
        # Main Node
        nodes.append({"pos": [670, 240], "label": "LOCAL_NODE", "size": 6})
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
            
            self.targets = new_targets
            self._update_topology_nodes()
        except: pass

    def _update_topology_nodes(self):
        core = self._nodes[0]
        self._nodes = [core]
        
        for i, t in enumerate(self.targets[:15]):
            angle = (i / min(len(self.targets), 15)) * 2 * math.pi
            dist = 90 + (i % 3) * 20
            x = core["pos"][0] + math.cos(angle) * dist
            y = core["pos"][1] + math.sin(angle) * dist
            self._nodes.append({
                "pos": [x, y],
                "label": t.get("ip", "0.0.0.0"),
                "size": 3,
                "vulns": int(t.get("vulns", 0) or 0) > 0
            })

    def _start_ragnar(self):
        core_script = os.path.join(RAGNAR_DIR, "Ragnar.py")
        if not os.path.exists(core_script):
            self.status_msg = "ERR: CORE_MISSING_RUN_INSTALL"
            return

        # Selection logic for python binary
        if os.path.exists(RAGNAR_VENV_EXEC):
            python_bin = RAGNAR_VENV_EXEC
        else:
            python_bin = RAGNAR_SYS_EXEC
            self.status_msg = "WARN: USING_SYS_PYTHON"

        self.phase = PHASE_HUD
        self.history.clear()
        self.history.append(f"[INIT] {time.strftime('%H:%M:%S')} :: ESTABLISHING AUDIT KERNEL...")
        self.history.append(f"[INFO] BINARY: {python_bin}")
        
        cmd = ["sudo", python_bin, "Ragnar.py"]
        
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
            
            self.status_msg = "LINK_ESTABLISHED"
        except Exception as e:
            self.status_msg = f"ERR: {e}"

    def _read_output(self):
        while not self._stop_event.is_set() and self.master_fd:
            r, _, _ = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 1024).decode("utf-8", "replace")
                    if data:
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        for line in clean_data.splitlines():
                            if line.strip():
                                self.history.append(f" {line}")
                                if "points" in line.lower() or "finding" in line.lower():
                                    self._load_gamification()
                except OSError: break

    def _poll_data(self):
        while not self._stop_event.is_set():
            self._load_gamification()
            self._load_targets()
            self._cpu_history.append(random.uniform(20, 60) if self.process else random.uniform(5, 12))
            time.sleep(4)

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
                self.status_msg = "LINK_TERMINATED"
            else: self.dismissed = True
            return
            
        if self.phase == PHASE_LANDING:
            if ev.button is Button.A: self._start_ragnar()
            elif ev.button is Button.X: 
                self.status_msg = "MOBILE_BRIDGE_ACTIVE"
                self.phase = PHASE_QR
        
        elif self.phase == PHASE_HUD:
            if ev.button is Button.X: self.phase = PHASE_TARGETS
            elif ev.button is Button.Y: self.phase = PHASE_CONFIG
            elif ev.button in (Button.A, Button.START):
                ctx.get_input("OPERATOR COMMAND", self._on_terminal_input)
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
                    ctx.get_input("OPENAI_API_KEY", self._on_openai_input, self.openai_key)
                else:
                    self.attacks[opt] = not self.attacks[opt]

    def _on_openai_input(self, text: str | None):
        if text is not None:
            self.openai_key = text
            self._save_env()

    def _on_terminal_input(self, text: str | None):
        if text is not None: self._send_input(text)

    def _draw_hud_frame(self, surf: pygame.Surface):
        # Professional technical frame
        color = theme.ACCENT
        glow = theme.FG
        
        # Corner brackets
        bw, bh = 40, 40
        thickness = 3
        # Top-Left
        pygame.draw.lines(surf, color, False, [(0, bh), (0, 0), (bw, 0)], thickness)
        # Top-Right
        pygame.draw.lines(surf, color, False, [(theme.SCREEN_W-bw, 0), (theme.SCREEN_W-1, 0), (theme.SCREEN_W-1, bh)], thickness)
        # Bottom-Left
        pygame.draw.lines(surf, color, False, [(0, theme.SCREEN_H-bh), (0, theme.SCREEN_H-1), (bw, theme.SCREEN_H-1)], thickness)
        # Bottom-Right
        pygame.draw.lines(surf, color, False, [(theme.SCREEN_W-bw, theme.SCREEN_H-1), (theme.SCREEN_W-1, theme.SCREEN_H-1), (theme.SCREEN_W-1, theme.SCREEN_H-bh)], thickness)
        
        # Side bars
        pygame.draw.line(surf, theme.DIVIDER, (0, 100), (0, theme.SCREEN_H-100), 1)
        pygame.draw.line(surf, theme.DIVIDER, (theme.SCREEN_W-1, 100), (theme.SCREEN_W-1, theme.SCREEN_H-100), 1)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        self._frame_count += 1
        
        # HUD Frame
        self._draw_hud_frame(surf)
        
        # Header reading
        head_h = 60
        surf.blit(self.f_title.render("RAGNAR // AUDIT_ENGINE_v4", True, theme.FG), (theme.PADDING + 10, 15))
        
        # Rank / Credits (Gamification but cleaner)
        rank_x = 420
        surf.blit(self.f_tiny.render(f"ENTITY_LVL: {self.level:02d}", True, theme.ACCENT), (rank_x, 15))
        surf.blit(self.f_tiny.render(f"CREDITS: {self.points:06d}", True, theme.FG_DIM), (rank_x, 30))
        
        # CPU/Load Graph (mini)
        gx, gy = rank_x + 130, 15
        pygame.draw.rect(surf, theme.BG_ALT, (gx, gy, 120, 25))
        for i, val in enumerate(self._cpu_history):
            h = int(val * 0.2)
            pygame.draw.line(surf, theme.ACCENT, (gx + i*2 + 10, gy + 22), (gx + i*2 + 10, gy + 22 - h))
        surf.blit(self.f_tiny.render("PROC_LOAD", True, theme.FG_DIM), (gx + 130, gy + 5))

        if self.phase == PHASE_LANDING: self._render_landing(surf, head_h)
        elif self.phase == PHASE_HUD: self._render_hud(surf, head_h)
        elif self.phase == PHASE_TARGETS: self._render_targets(surf, head_h)
        elif self.phase == PHASE_DETAILS: self._render_details(surf, head_h)
        elif self.phase == PHASE_CONFIG: self._render_config(surf, head_h)
        elif self.phase == PHASE_QR: self._render_qr(surf, head_h)

        # Tech Footer
        foot_h = 30
        pygame.draw.line(surf, theme.DIVIDER, (20, theme.SCREEN_H - foot_h), (theme.SCREEN_W - 20, theme.SCREEN_H - foot_h))
        status_col = theme.ACCENT if "ACTIVE" in self.status_msg or "ESTABLISHED" in self.status_msg else theme.WARN
        surf.blit(self.f_small.render(f"SYSTEM_STATE: {self.status_msg}", True, status_col), (25, theme.SCREEN_H - 24))
        
        hints = {
            PHASE_LANDING: "A: INITIATE_LINK  X: MOBILE_BRIDGE  B: SHUTDOWN",
            PHASE_HUD: "X: TARGET_LIST  Y: PROTOCOLS  A: COMMAND  B: DISCONNECT",
            PHASE_TARGETS: "X: PROTOCOLS  Y: REALTIME_HUD  A: INSPECT  B: DISCONNECT",
            PHASE_DETAILS: "B: RETURN",
            PHASE_CONFIG: "X: REALTIME_HUD  Y: TARGET_LIST  A: TOGGLE  B: DISCONNECT",
            PHASE_QR: "B: RETURN"
        }
        h_surf = self.f_small.render(hints.get(self.phase, "B: BACK"), True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 25, theme.SCREEN_H - 24))

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        # High-fidelity tech splash
        bx, by = theme.SCREEN_W // 2 - 250, head_h + 40
        bw, bh = 500, 240
        pygame.draw.rect(surf, theme.BG_ALT, (bx, by, bw, bh), border_radius=4)
        pygame.draw.rect(surf, theme.DIVIDER, (bx, by, bw, bh), 1, border_radius=4)
        
        core_script = os.path.join(RAGNAR_DIR, "Ragnar.py")
        installed = os.path.exists(core_script)
        
        lines = [
            "RAGNAR // AUTONOMOUS NETWORK AUDITOR",
            "ARCHITECTURE: MULTI-AGENT REASONING",
            "-------------------------------------",
            f"VULNS_ARCHIVED:  {self.stats['vulns']:03d}",
            f"IDENT_ENTITIES:  {len(self.targets):03d}",
            "-------------------------------------",
            "STATUS: AUDIT_KERNEL_READY" if installed else "STATUS: AUDIT_KERNEL_MISSING",
            ">> PRESS A TO ESTABLISH NEURAL_LINK <<" if installed else ">> RUN 'INSTALL RAGNAR' IN SETTINGS <<",
            ">> PRESS X FOR WEB_BRIDGE ACCESS   <<"
        ]
        for i, ln in enumerate(lines):
            col = theme.ACCENT if ">>" in ln else theme.FG
            if "RAGNAR" in ln: col = theme.FG; font = self.f_bold
            else: font = self.f_main
            surf.blit(font.render(ln, True, col), (bx + 40, by + 30 + i * 26))
            
        # Pulsing decoration
        pulse = abs(math.sin(self._frame_count * 0.05))
        pygame.draw.circle(surf, (int(90*pulse), int(230*pulse), int(170*pulse)), (bx + 25, by + 42), 4)

    def _render_hud(self, surf: pygame.Surface, head_h: int):
        # Data Stream
        log_rect = pygame.Rect(20, head_h + 10, 500, theme.SCREEN_H - head_h - 60)
        pygame.draw.rect(surf, (5, 6, 10), log_rect, border_radius=2)
        pygame.draw.rect(surf, theme.DIVIDER, log_rect, 1)
        
        line_h = 18
        max_lines = log_rect.height // line_h - 1
        visible = list(self.history)[-max_lines:]
        for i, line in enumerate(visible):
            col = theme.FG
            if "[INIT]" in line: col = theme.ACCENT
            elif "finding" in line.lower() or "success" in line.lower(): col = (100, 255, 150)
            elif "err" in line.lower() or "fail" in line.lower(): col = theme.ERR
            surf.blit(self.f_tiny.render(line[:85], True, col), (log_rect.x + 10, log_rect.y + 8 + i * line_h))

        # Topology / Signals
        self._render_topology(surf, head_h)
        self._render_signals(surf, head_h)

    def _render_topology(self, surf: pygame.Surface, head_h: int):
        pane = pygame.Rect(535, head_h + 10, 245, 230)
        pygame.draw.rect(surf, theme.BG_ALT, pane, border_radius=2)
        pygame.draw.rect(surf, theme.DIVIDER, pane, 1)
        surf.blit(self.f_tiny.render("NODE_TOPOLOGY", True, theme.FG_DIM), (pane.x + 10, pane.y + 5))
        
        core = self._nodes[0]
        # Draw connections
        for node in self._nodes[1:]:
            pygame.draw.line(surf, (30, 40, 50), core["pos"], node["pos"], 1)
            col = theme.ERR if node.get("vulns") else theme.ACCENT
            pygame.draw.circle(surf, col, (int(node["pos"][0]), int(node["pos"][1])), node["size"])
            if self._frame_count % 60 < 30: # Blink label for vulnerables
                label = self.f_tiny.render(node["label"].split(".")[-1], True, theme.FG_DIM)
                surf.blit(label, (node["pos"][0] + 5, node["pos"][1] - 5))
            
        pygame.draw.circle(surf, theme.FG, core["pos"], core["size"], 2)
        # Scan sweep
        rad = (self._frame_count * 0.05) % (math.pi * 2)
        end = (core["pos"][0] + math.cos(rad) * 110, core["pos"][1] + math.sin(rad) * 110)
        pygame.draw.line(surf, (40, 80, 70), core["pos"], end, 1)

    def _render_signals(self, surf: pygame.Surface, head_h: int):
        pane = pygame.Rect(535, head_h + 250, 245, 110)
        pygame.draw.rect(surf, theme.BG_ALT, pane, border_radius=2)
        pygame.draw.rect(surf, theme.DIVIDER, pane, 1)
        surf.blit(self.f_tiny.render("SIGNAL_INTENSITY", True, theme.FG_DIM), (pane.x + 10, pane.y + 5))
        
        # Oscilloscope-style wave
        points = []
        for x in range(pane.width - 20):
            y = pane.centery + math.sin((self._frame_count + x) * 0.1) * 20 * math.sin(self._frame_count * 0.02)
            points.append((pane.x + 10 + x, y))
        if len(points) > 1:
            pygame.draw.lines(surf, theme.ACCENT, False, points, 1)
            
        # Readouts
        surf.blit(self.f_tiny.render(f"PPS: {random.randint(120, 450)}", True, theme.FG), (pane.x + 15, pane.bottom - 20))
        surf.blit(self.f_tiny.render(f"LAT: {random.randint(5, 45)}ms", True, theme.FG), (pane.right - 60, pane.bottom - 20))

    def _render_targets(self, surf: pygame.Surface, head_h: int):
        box = pygame.Rect(20, head_h + 10, 760, theme.SCREEN_H - head_h - 60)
        pygame.draw.rect(surf, theme.BG_ALT, box, border_radius=4)
        pygame.draw.rect(surf, theme.DIVIDER, box, 1)
        
        if not self.targets:
            surf.blit(self.f_main.render("SEARCHING FOR NETWORK ENTITIES...", True, theme.FG_DIM), (theme.SCREEN_W // 2 - 140, theme.SCREEN_H // 2))
            return

        headers = [("IP_ADDR", 130), ("IDENTIFIER", 180), ("HARDWARE", 160), ("THREAT", 70), ("OS_PLATFORM", 100)]
        hx = box.x + 20
        for name, width in headers:
            surf.blit(self.f_small.render(name, True, theme.ACCENT), (hx, box.y + 15))
            hx += width
            
        pygame.draw.line(surf, theme.DIVIDER, (box.x + 15, box.y + 38), (box.right - 15, box.y + 38))
        
        row_h = 26
        visible_count = (box.height - 55) // row_h
        start = max(0, min(self.target_cursor - visible_count // 2, len(self.targets) - visible_count))
        
        for i in range(visible_count):
            idx = start + i
            if idx >= len(self.targets): break
            t = self.targets[idx]
            y = box.y + 45 + i * row_h
            
            if idx == self.target_cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, (box.x + 10, y - 2, box.width - 20, row_h), border_radius=2)
                pygame.draw.rect(surf, theme.ACCENT, (box.x + 10, y - 2, box.width - 20, row_h), 1, border_radius=2)
            
            tx = box.x + 20
            surf.blit(self.f_small.render(t.get("ip", "??"), True, theme.FG), (tx, y))
            tx += 130
            surf.blit(self.f_small.render(t.get("hostname", "??")[:22], True, theme.FG), (tx, y))
            tx += 180
            surf.blit(self.f_small.render(t.get("vendor", "??")[:20], True, theme.FG_DIM), (tx, y))
            tx += 160
            v_val = int(t.get("vulns", 0) or 0)
            vcol = theme.ERR if v_val > 0 else theme.FG_DIM
            surf.blit(self.f_small.render(f"HIGH" if v_val > 0 else "LOW", True, vcol), (tx, y))
            tx += 70
            surf.blit(self.f_small.render(t.get("os", "??")[:14], True, theme.FG_DIM), (tx, y))

    def _render_details(self, surf: pygame.Surface, head_h: int):
        if not self.targets: return
        t = self.targets[min(self.target_cursor, len(self.targets)-1)]
        
        box = pygame.Rect(100, head_h + 30, 600, 320)
        pygame.draw.rect(surf, theme.BG_ALT, box, border_radius=4)
        pygame.draw.rect(surf, theme.ACCENT, box, 1, border_radius=4)
        
        surf.blit(self.f_bold.render(f"ENTITY_PROFILE: {t.get('ip', '??')}", True, theme.ACCENT), (box.x + 30, box.y + 20))
        
        fields = [
            ("HOSTNAME", t.get('hostname')),
            ("MAC_ADDRESS", t.get('mac')),
            ("VENDOR_IDENT", t.get('vendor')),
            ("OS_FINGERPRINT", t.get('os')),
            ("ACTIVE_SERVICES", t.get('services')),
            ("VULNERABILITIES", t.get('vulns')),
            ("LAST_OBSERVED", t.get('last_seen'))
        ]
        for i, (label, val) in enumerate(fields):
            surf.blit(self.f_small.render(f"{label:16} ::", True, theme.FG_DIM), (box.x + 40, box.y + 70 + i * 28))
            vcol = theme.ERR if label == "VULNERABILITIES" and int(val or 0) > 0 else theme.FG
            surf.blit(self.f_small.render(str(val or "UNKNOWN"), True, vcol), (box.x + 200, box.y + 70 + i * 28))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        y = head_h + 20
        surf.blit(self.f_bold.render("PROTOCOL_ORCHESTRATION", True, theme.ACCENT), (50, y))
        for i, opt in enumerate(self.config_options):
            sel = i == self.config_cursor
            rect = pygame.Rect(50, y + 45 + i*38, 480, 32)
            if sel: 
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=2)
                pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=2)
            else:
                pygame.draw.rect(surf, theme.BG_ALT, rect, border_radius=2)
                pygame.draw.rect(surf, theme.DIVIDER, rect, 1, border_radius=2)
                
            surf.blit(self.f_main.render(f"{opt}", True, theme.FG if not sel else theme.ACCENT), (70, rect.y + 5))
            
            if opt in self.attacks:
                val = self.attacks[opt]
                status = "ENABLED" if val else "DISABLED"
                scol = theme.ACCENT if val else theme.FG_DIM
                surf.blit(self.f_main.render(status, True, scol), (rect.right - 100, rect.y + 5))
            elif opt == "EDIT_OPENAI_KEY":
                masked = (self.openai_key[:12] + "...") if self.openai_key else "UNDEFINED"
                surf.blit(self.f_small.render(masked, True, theme.ACCENT if self.openai_key else theme.ERR), (rect.right - 180, rect.y + 8))

    def _render_qr(self, surf: pygame.Surface, head_h: int):
        ip = qr.lan_ipv4() or "127.0.0.1"
        url = f"http://{ip}:8000"
        
        box_w, box_h = 420, 340
        bx, by = (theme.SCREEN_W - box_w) // 2, (theme.SCREEN_H - box_h) // 2 + 20
        pygame.draw.rect(surf, theme.BG_ALT, (bx, by, box_w, box_h), border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT, (bx, by, box_w, box_h), 1, border_radius=8)
        
        surf.blit(self.f_bold.render("MOBILE_BRIDGE_ACCESS", True, theme.ACCENT), (bx + 85, by + 20))
        
        matrix = qr.make_matrix(url)
        if matrix:
            m_size = len(matrix)
            cell = min(200 // m_size, 7)
            qx, qy = bx + (box_w - m_size * cell) // 2, by + 60
            for r, row in enumerate(matrix):
                for c, val in enumerate(row):
                    if val: pygame.draw.rect(surf, theme.FG, (qx + c*cell, qy + r*cell, cell, cell))
        
        surf.blit(self.f_main.render(url, True, theme.ACCENT), (bx + (box_w - self.f_main.size(url)[0]) // 2, by + box_h - 70))
        surf.blit(self.f_tiny.render("SCAN TO BRIDGE HUD TO EXTERNAL DEVICE", True, theme.FG_DIM), (bx + 80, by + box_h - 45))
        if not self.process:
            surf.blit(self.f_tiny.render("(ENSURE NEURAL_LINK IS ACTIVE)", True, theme.WARN), (bx + 110, by + box_h - 30))
