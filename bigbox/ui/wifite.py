"""Wifite — Interactive automated wireless auditor with high-fidelity Scifi UI."""
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
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Dict

import pygame

from bigbox import theme, hardware
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_LANDING = "landing"
PHASE_CONFIG = "config"
PHASE_SCANNING = "scanning"
PHASE_TARGETS = "targets"
PHASE_ATTACKING = "attacking"

@dataclass
class WifiteTarget:
    id: int
    ssid: str
    bssid: str
    channel: str
    encryption: str
    power: str
    clients: str

class WifiteView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.history = deque(maxlen=200)
        self.status_msg = "CORE_IDLE"
        
        # UI dimensions
        self.f_title = pygame.font.Font(None, 42)
        self.f_med = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 18)
        self.f_tiny = pygame.font.Font(None, 14)
        
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

        # Targets list
        self.targets: List[WifiteTarget] = []
        self.target_cursor = 0
        self.target_scroll = 0
        
        # Process management
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None
        
        self.cursor = 0 # Config cursor
        self.scroll_idx = 0
        
        # Aesthetics
        self._grid_surf = self._create_grid_bg()
        self._scan_y = 0
        self._noise_timer = 0

    def _create_grid_bg(self) -> pygame.Surface:
        s = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H))
        s.fill((5, 5, 10))
        for x in range(0, theme.SCREEN_W, 40):
            pygame.draw.line(s, (15, 15, 30), (x, 0), (x, theme.SCREEN_H))
        for y in range(0, theme.SCREEN_H, 40):
            pygame.draw.line(s, (15, 15, 30), (0, y), (theme.SCREEN_W, y))
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
            self.status_msg = "ERROR: WORDLIST_MISSING"
            return

        self.phase = PHASE_SCANNING
        self.history.clear()
        self.targets.clear()
        
        cmd = ["wifite", "-i", self.selected_iface] + self._get_full_args()
        
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
            self.status_msg = "SCANNING_RECON..."
        except Exception as e:
            self.status_msg = f"LAUNCH_FAIL: {e}"

    def _read_output(self):
        # Regex to parse wifite target lines:
        # NUM  ESSID            BSSID              CHN  ENCR  PWR  CLIENTS
        # 1    NetworkName      AA:BB:CC:DD:EE:FF  1    WPA2  -45  2
        target_re = re.compile(r"^\s*(\d+)\s+(.*?)\s+([0-9A-F:]{17})\s+(\d+)\s+(\w+)\s+(-\d+)\s+(\d+)")

        while not self._stop_event.is_set() and self.master_fd:
            r, w, e = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 4096).decode("utf-8", "replace")
                    if data:
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        for line in clean_data.splitlines():
                            stripped = line.strip()
                            if not stripped: continue
                            
                            self.history.append(stripped)
                            
                            # Parse target
                            m = target_re.match(stripped)
                            if m:
                                tid, ssid, bssid, chan, enc, pwr, clis = m.groups()
                                # Check if already in list
                                found = False
                                for t in self.targets:
                                    if t.bssid == bssid:
                                        t.ssid = ssid
                                        t.power = pwr
                                        t.clients = clis
                                        found = True
                                        break
                                if not found:
                                    self.targets.append(WifiteTarget(int(tid), ssid, bssid, chan, enc, pwr, clis))

                            if "select target" in line.lower() or "enter number" in line.lower():
                                if self.phase == PHASE_SCANNING:
                                    self.phase = PHASE_TARGETS
                                    self.status_msg = "TARGETS_ACQUIRED"
                except OSError:
                    break

    def _send_ctrl_c(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
            except: pass

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
        hardware.ensure_wifi_managed()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        
        if ev.button is Button.B:
            if self.phase in (PHASE_SCANNING, PHASE_TARGETS, PHASE_ATTACKING):
                self._cleanup()
                self.phase = PHASE_LANDING
                self.status_msg = "AUDIT_ABORTED"
            elif self.phase == PHASE_CONFIG:
                self.phase = PHASE_LANDING
            else:
                self.dismissed = True
            return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.A: self._start_wifite()
            elif ev.button is Button.X: self.phase = PHASE_CONFIG
        
        elif self.phase == PHASE_CONFIG:
            if ev.button is Button.UP: self.cursor = (self.cursor - 1) % 7
            elif ev.button is Button.DOWN: self.cursor = (self.cursor + 1) % 7
            elif ev.button is Button.A: self._toggle_config_option(ctx)
            elif ev.button is Button.START: self.phase = PHASE_LANDING

        elif self.phase == PHASE_SCANNING:
            if ev.button is Button.LL: self._send_ctrl_c()

        elif self.phase == PHASE_TARGETS:
            if not self.targets: return
            if ev.button is Button.UP:
                self.target_cursor = (self.target_cursor - 1) % len(self.targets)
                self._adjust_target_scroll()
            elif ev.button is Button.DOWN:
                self.target_cursor = (self.target_cursor + 1) % len(self.targets)
                self._adjust_target_scroll()
            elif ev.button is Button.A:
                t = self.targets[self.target_cursor]
                self._send_input(str(t.id))
                self.phase = PHASE_ATTACKING
                self.status_msg = f"ENGAGING_{t.ssid or t.bssid}"

        elif self.phase == PHASE_ATTACKING:
            if ev.button is Button.X: self._send_ctrl_c()

    def _adjust_target_scroll(self):
        visible = 8
        if self.target_cursor < self.target_scroll:
            self.target_scroll = self.target_cursor
        elif self.target_cursor >= self.target_scroll + visible:
            self.target_scroll = self.target_cursor - visible + 1

    def _toggle_config_option(self, ctx: App):
        # 0: Iface, 1: WPS, 2: WPA, 3: PMKID, 4: Pixie, 5: Kill, 6: Custom
        if self.cursor == 0:
            active = hardware.list_wifi_clients() + hardware.list_monitor_ifaces()
            ifaces = sorted(list(set(["wlan0", "wlan1"] + active)))
            idx = (ifaces.index(self.selected_iface) + 1) % len(ifaces) if self.selected_iface in ifaces else 0
            self.selected_iface = ifaces[idx]
        elif self.cursor == 1: self.opt_wps = not self.opt_wps
        elif self.cursor == 2: self.opt_wpa = not self.opt_wpa
        elif self.cursor == 3: self.opt_pmkid = not self.opt_pmkid
        elif self.cursor == 4: self.opt_pixie = not self.opt_pixie
        elif self.cursor == 5: self.opt_kill = not self.opt_kill
        elif self.cursor == 6:
            ctx.get_input("CUSTOM_ARGS", lambda v: setattr(self, "custom_args", v or ""), initial=self.custom_args)

    def _send_input(self, text: str):
        if self.master_fd and text:
            os.write(self.master_fd, (text + "\n").encode())

    def render(self, surf: pygame.Surface) -> None:
        surf.blit(self._grid_surf, (0, 0))
        self._render_header(surf)

        if self.phase == PHASE_LANDING: self._render_landing(surf)
        elif self.phase == PHASE_CONFIG: self._render_config(surf)
        elif self.phase == PHASE_SCANNING: self._render_scanning(surf)
        elif self.phase == PHASE_TARGETS: self._render_targets(surf)
        elif self.phase == PHASE_ATTACKING: self._render_attacking(surf)

        self._render_footer(surf)

    def _render_header(self, surf: pygame.Surface):
        head_h = 60
        pygame.draw.rect(surf, (10, 10, 20), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        
        # Cyberpunk scanlines on header
        for y in range(0, head_h, 4):
            pygame.draw.line(surf, (0, 20, 10), (0, y), (theme.SCREEN_W, y))

        tag = self.f_med.render("GHOST_PROTOCOL // AUDITOR", True, theme.ACCENT_DIM)
        surf.blit(tag, (theme.PADDING, 6))
        title = self.f_title.render("WIFITE :: NEURAL_LINK", True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, 24))
        
        # System uptime / clock
        ts = datetime.now().strftime("%H:%M:%S")
        surf.blit(self.f_med.render(ts, True, theme.ACCENT), (theme.SCREEN_W - 100, 20))

    def _render_footer(self, surf: pygame.Surface):
        foot_h = 35
        fy = theme.SCREEN_H - foot_h
        pygame.draw.rect(surf, (5, 5, 15), (0, fy, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER, (0, fy), (theme.SCREEN_W, fy))
        
        # Pulsing status
        pulse = int(127 + 128 * math.sin(time.time() * 5))
        st_col = theme.ACCENT if self.phase != PHASE_ATTACKING else theme.WARN
        st_surf = self.f_med.render(f">> {self.status_msg}", True, st_col)
        st_surf.set_alpha(pulse)
        surf.blit(st_surf, (15, fy + 8))
        
        hint = self._get_hint()
        h_surf = self.f_med.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 15, fy + 8))

    def _get_hint(self) -> str:
        if self.phase == PHASE_LANDING: return "A: INITIATE  X: CONFIG  B: EXIT"
        if self.phase == PHASE_CONFIG: return "UP/DN: NAV  A: TOGGLE  START: DONE"
        if self.phase == PHASE_SCANNING: return "LL: LOCK_TARGETS  B: ABORT"
        if self.phase == PHASE_TARGETS: return "UP/DN: SELECT  A: ENGAGE  B: ABORT"
        if self.phase == PHASE_ATTACKING: return "X: SKIP_ATTACK  B: STOP"
        return "B: BACK"

    def _render_landing(self, surf: pygame.Surface):
        y = 120
        box = pygame.Rect(theme.SCREEN_W // 2 - 250, y, 500, 220)
        pygame.draw.rect(surf, (15, 15, 30), box, border_radius=12)
        pygame.draw.rect(surf, theme.ACCENT_DIM, box, 1, border_radius=12)
        
        attacks = []
        if self.opt_wps: attacks.append("WPS")
        if self.opt_pixie: attacks.append("PIXIE")
        if self.opt_wpa: attacks.append("WPA")
        
        lines = [
            f"LINK_NODE: {self.selected_iface}",
            f"PAYLOADS: {', '.join(attacks) or 'NONE'}",
            f"DICT: rockyou.txt",
            "---------------------------",
            "SYSTEM_READY_FOR_RECON"
        ]
        for i, ln in enumerate(lines):
            col = theme.ACCENT if "READY" in ln else theme.FG
            surf.blit(self.f_med.render(ln, True, col), (box.x + 40, box.y + 40 + i * 32))

    def _render_config(self, surf: pygame.Surface):
        y = 80
        opts = [
            ("INTERFACE", self.selected_iface),
            ("ATTACK_WPS", "ENABLED" if self.opt_wps else "DISABLED"),
            ("ATTACK_WPA", "ENABLED" if self.opt_wpa else "DISABLED"),
            ("ATTACK_PMKID", "ENABLED" if self.opt_pmkid else "DISABLED"),
            ("PIXIE_DUST", "ENABLED" if self.opt_pixie else "DISABLED"),
            ("KILL_CONFLICTS", "ENABLED" if self.opt_kill else "DISABLED"),
            ("CUSTOM_STR", self.custom_args or "(none)"),
        ]
        for i, (lbl, val) in enumerate(opts):
            sel = i == self.cursor
            rect = pygame.Rect(50, y + i*45, 450, 38)
            if sel:
                pygame.draw.rect(surf, (30, 30, 60), rect, border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=4)
            surf.blit(self.f_med.render(f"{lbl}:", True, theme.FG_DIM), (70, rect.y + 10))
            surf.blit(self.f_med.render(str(val), True, theme.ACCENT if sel else theme.FG), (250, rect.y + 10))

    def _render_scanning(self, surf: pygame.Surface):
        # Tactical scanning animation
        self._scan_y = (self._scan_y + 5) % (theme.SCREEN_H - 100)
        pygame.draw.line(surf, (0, 255, 200, 100), (0, 80 + self._scan_y), (theme.SCREEN_W, 80 + self._scan_y), 2)
        
        # Show mini terminal at bottom
        term_rect = pygame.Rect(20, 100, theme.SCREEN_W - 40, 300)
        pygame.draw.rect(surf, (0, 0, 0, 150), term_rect, border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT_DIM, term_rect, 1, border_radius=8)
        
        visible = list(self.history)[-12:]
        for i, line in enumerate(visible):
            surf.blit(self.f_small.render(line[:90], True, theme.ACCENT), (term_rect.x + 20, term_rect.y + 20 + i*22))

    def _render_targets(self, surf: pygame.Surface):
        y = 80
        surf.blit(self.f_med.render("SIGNAL_LOCK_IDENTIFIED:", True, theme.ACCENT), (30, y))
        
        list_rect = pygame.Rect(20, y + 30, theme.SCREEN_W - 40, 300)
        pygame.draw.rect(surf, (10, 10, 20), list_rect, border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT_DIM, list_rect, 1, border_radius=8)
        
        # Headers
        hy = list_rect.y + 10
        surf.blit(self.f_tiny.render("ID", True, theme.FG_DIM), (40, hy))
        surf.blit(self.f_tiny.render("ESSID (NETWORK NAME)", True, theme.FG_DIM), (80, hy))
        surf.blit(self.f_tiny.render("ENCR", True, theme.FG_DIM), (400, hy))
        surf.blit(self.f_tiny.render("PWR", True, theme.FG_DIM), (500, hy))
        surf.blit(self.f_tiny.render("CLIS", True, theme.FG_DIM), (580, hy))
        
        row_y = hy + 20
        visible = self.targets[self.target_scroll : self.target_scroll + 8]
        for i, t in enumerate(visible):
            idx = self.target_scroll + i
            sel = idx == self.target_cursor
            ry = row_y + i * 32
            
            if sel:
                pygame.draw.rect(surf, (40, 40, 80), (30, ry-2, list_rect.width-20, 28), border_radius=4)
            
            color = theme.ACCENT if sel else theme.FG
            surf.blit(self.f_med.render(str(t.id), True, color), (40, ry))
            surf.blit(self.f_med.render(t.ssid[:25], True, color), (80, ry))
            surf.blit(self.f_med.render(t.encryption, True, theme.FG_DIM), (400, ry))
            surf.blit(self.f_med.render(f"{t.power}dBm", True, color), (500, ry))
            surf.blit(self.f_med.render(t.clients, True, theme.FG_DIM), (580, ry))

    def _render_attacking(self, surf: pygame.Surface):
        # HUD for active attack
        y = 100
        center_x = theme.SCREEN_W // 2
        
        # Animated circle
        angle = time.time() * 2
        radius = 80
        cx = center_x
        cy = y + 100
        pygame.draw.circle(surf, theme.ACCENT_DIM, (cx, cy), radius, 2)
        # Pulse lines
        for i in range(4):
            a = angle + (i * (math.pi/2))
            px = cx + math.cos(a) * radius
            py = cy + math.sin(a) * radius
            pygame.draw.line(surf, theme.ACCENT, (cx, cy), (px, py), 2)

        # Log
        log_rect = pygame.Rect(20, 280, theme.SCREEN_W - 40, 140)
        pygame.draw.rect(surf, (0, 0, 0, 180), log_rect, border_radius=8)
        
        visible = list(self.history)[-5:]
        for i, line in enumerate(visible):
            surf.blit(self.f_small.render(f"> {line[:90]}", True, theme.ACCENT), (40, log_rect.y + 15 + i * 24))
