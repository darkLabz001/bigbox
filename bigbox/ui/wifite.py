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
import json
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
PHASE_LOOT = "loot"

GAMIFICATION_PATH = "/opt/ragnar/data/gamification.json"

@dataclass
class WifiteTarget:
    id: int
    ssid: str
    bssid: str
    channel: str
    encryption: str
    power: str
    clients: str
    power_history: List[int] = field(default_factory=list)

class WifiteView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.history = deque(maxlen=200)
        self.status_msg = "CORE_IDLE"
        
        # Gamification
        self.coins = 0
        self._load_coins()
        
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
        
        # Loot list
        self.loot_list: List[str] = []
        self.loot_cursor = 0
        
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

    def _load_coins(self):
        if os.path.exists(GAMIFICATION_PATH):
            try:
                with open(GAMIFICATION_PATH, "r") as f:
                    self.coins = json.load(f).get("total_points", 0)
            except: pass

    def _award_coins(self, amount: int):
        self.coins += amount
        if os.path.exists(GAMIFICATION_PATH):
            try:
                with open(GAMIFICATION_PATH, "r+") as f:
                    data = json.load(f)
                    data["total_points"] = data.get("total_points", 0) + amount
                    f.seek(0)
                    json.dump(data, f, indent=4)
                    f.truncate()
            except: pass
        self.status_msg = f"LOOT_SECURED: +{amount} COINS"

    def _refresh_loot(self):
        self.loot_list = []
        search_dirs = [os.path.expanduser("~/hs"), "/root/hs", "hs", "handshakes"]
        for d in search_dirs:
            if os.path.isdir(d):
                files = [f for f in os.listdir(d) if f.endswith((".cap", ".csv", ".pcap", ".txt"))]
                self.loot_list.extend(files)
        self.loot_list = sorted(list(set(self.loot_list)), reverse=True)

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
        self.is_scanning = True
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
        # Improved Lenient Regex for target lines
        target_re = re.compile(r"^\s*(\d+)\s+([0-9A-F:]{17}|.*?)\s+([0-9A-F:]{17}|.*?)\s+(\d+)\s+(\w+)\s+(-\d+)\s+(\d+)", re.IGNORECASE)
        
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
                            
                            # More flexible target identification
                            parts = stripped.split()
                            if len(parts) >= 6 and parts[0].isdigit() and (":" in parts[1] or ":" in parts[2]):
                                try:
                                    tid = int(parts[0])
                                    # Identify BSSID vs SSID by searching for ':'
                                    bssid_idx = 1 if ":" in parts[1] else 2
                                    bssid = parts[bssid_idx].upper()
                                    ssid = parts[bssid_idx-1] if bssid_idx == 2 else parts[0] # Very fallback
                                    
                                    # Find power (usually starts with -)
                                    pwr = "-100"
                                    for p in parts:
                                        if p.startswith("-") and p[1:].isdigit():
                                            pwr = p
                                            break
                                    
                                    found = False
                                    for t in self.targets:
                                        if t.bssid == bssid:
                                            t.power = pwr
                                            t.power_history.append(int(pwr))
                                            if len(t.power_history) > 20: t.power_history.pop(0)
                                            found = True
                                            break
                                    if not found:
                                        self.targets.append(WifiteTarget(tid, ssid, bssid, "0", "WPA", pwr, "0", [int(pwr)]))
                                except: pass

                            if "captured" in stripped.lower() or "cracked" in stripped.lower():
                                self._award_coins(50)

                            if "select target" in stripped.lower() or "enter number" in stripped.lower():
                                if self.phase == PHASE_SCANNING:
                                    self.phase = PHASE_TARGETS
                                    self.status_msg = "SPECTRUM_LOCKED"
                except OSError: break

    def _send_input(self, text: str):
        if self.master_fd and text:
            os.write(self.master_fd, (text + "\n").encode())

    def _send_ctrl_c(self):
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                # Force switch to target selection if we are scanning
                if self.phase == PHASE_SCANNING:
                    self.phase = PHASE_TARGETS
                    self.status_msg = "MANUAL_OVERRIDE_ACTIVE"
            except: pass

    def _cleanup(self):
        self._stop_event.set()
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                time.sleep(0.5)
            except: pass
        if self.master_fd: os.close(self.master_fd)
        if self.slave_fd: os.close(self.slave_fd)
        self.master_fd = self.slave_fd = self.process = None
        hardware.ensure_wifi_managed()

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            if self.phase in (PHASE_SCANNING, PHASE_TARGETS, PHASE_ATTACKING, PHASE_LOOT):
                if self.phase != PHASE_LOOT: self._cleanup()
                self.phase = PHASE_LANDING
                self.status_msg = "AUDIT_TERMINATED"
            elif self.phase == PHASE_CONFIG: self.phase = PHASE_LANDING
            else: self.dismissed = True
            return
        if self.phase == PHASE_LANDING:
            if ev.button is Button.A: self._start_wifite()
            elif ev.button is Button.X: self.phase = PHASE_CONFIG
            elif ev.button is Button.Y:
                self.phase = PHASE_LOOT
                self._refresh_loot()
        elif self.phase == PHASE_CONFIG:
            if ev.button is Button.UP: self.cursor = (self.cursor - 1) % 7
            elif ev.button is Button.DOWN: self.cursor = (self.cursor + 1) % 7
            elif ev.button is Button.A: self._toggle_config_option(ctx)
            elif ev.button is Button.START: self.phase = PHASE_LANDING
        elif self.phase == PHASE_SCANNING:
            if ev.button is Button.LL: self._send_ctrl_c()
        elif self.phase == PHASE_TARGETS:
            if not self.targets: 
                if ev.button is Button.B: self._cleanup(); self.phase = PHASE_LANDING
                return
            if ev.button is Button.UP: self.target_cursor = (self.target_cursor - 1) % len(self.targets)
            elif ev.button is Button.DOWN: self.target_cursor = (self.target_cursor + 1) % len(self.targets)
            elif ev.button is Button.A:
                t = self.targets[self.target_cursor]
                self._send_input(str(t.id))
                self.phase = PHASE_ATTACKING
                self.status_msg = f"ENGAGING_{t.ssid or t.bssid}"
        elif self.phase == PHASE_LOOT:
            if ev.button is Button.UP: self.loot_cursor = max(0, self.loot_cursor - 1)
            elif ev.button is Button.DOWN: self.loot_cursor = min(len(self.loot_list)-1, self.loot_cursor + 1)

    def _toggle_config_option(self, ctx: App):
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

    def render(self, surf: pygame.Surface) -> None:
        surf.blit(self._grid_surf, (0, 0))
        self._render_header(surf)
        if self.phase == PHASE_LANDING: self._render_landing(surf)
        elif self.phase == PHASE_CONFIG: self._render_config(surf)
        elif self.phase == PHASE_SCANNING: self._render_scanning(surf)
        elif self.phase == PHASE_TARGETS: self._render_targets(surf)
        elif self.phase == PHASE_ATTACKING: self._render_attacking(surf)
        elif self.phase == PHASE_LOOT: self._render_loot(surf)
        self._render_footer(surf)

    def _render_header(self, surf: pygame.Surface):
        head_h = 60
        pygame.draw.rect(surf, (10, 10, 20), (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h-1), (theme.SCREEN_W, head_h-1), 2)
        surf.blit(self.f_title.render("WIFITE :: NEURAL_LINK", True, theme.ACCENT), (theme.PADDING, 18))
        xp_rect = pygame.Rect(theme.SCREEN_W - 220, 15, 200, 30)
        pygame.draw.rect(surf, (20, 40, 20), xp_rect, border_radius=4)
        pygame.draw.rect(surf, (0, 200, 100), (xp_rect.x+2, xp_rect.y+2, int(196*(self.coins%1000/1000)), 26), border_radius=2)
        surf.blit(self.f_tiny.render(f"COINS: {self.coins}  LVL: {self.coins//1000 + 1}", True, theme.FG), (xp_rect.x+10, xp_rect.y+8))

    def _render_footer(self, surf: pygame.Surface):
        fy = theme.SCREEN_H - 35
        pygame.draw.rect(surf, (5, 5, 15), (0, fy, theme.SCREEN_W, 35))
        pygame.draw.line(surf, theme.DIVIDER, (0, fy), (theme.SCREEN_W, fy))
        surf.blit(self.f_med.render(f">> {self.status_msg}", True, theme.ACCENT), (15, fy + 8))
        hint = self._get_hint()
        h_surf = self.f_med.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 15, fy + 8))

    def _get_hint(self) -> str:
        if self.phase == PHASE_LANDING: return "A: INITIATE  X: CONFIG  Y: LOOT  B: EXIT"
        if self.phase == PHASE_CONFIG: return "UP/DN: NAV  A: TOGGLE  START: DONE"
        if self.phase == PHASE_SCANNING: return "LL: LOCK_TARGETS"
        if self.phase == PHASE_TARGETS: return "UP/DN: SELECT  A: ENGAGE"
        if self.phase == PHASE_LOOT: return "UP/DN: BROWSE  B: BACK"
        return "B: BACK"

    def _render_landing(self, surf: pygame.Surface):
        box = pygame.Rect(theme.SCREEN_W // 2 - 250, 120, 500, 220)
        pygame.draw.rect(surf, (15, 15, 30), box, border_radius=12)
        pygame.draw.rect(surf, theme.ACCENT_DIM, box, 1, border_radius=12)
        lines = [f"LINK_NODE: {self.selected_iface}", f"XP_GAINED: {self.coins}", "---------------------------", "NEURAL_SYSTEM_ONLINE", "> PRESS A TO INITIATE"]
        for i, ln in enumerate(lines):
            col = theme.ACCENT if ">" in ln else theme.FG
            surf.blit(self.f_med.render(ln, True, col), (box.x + 40, box.y + 40 + i * 32))

    def _render_config(self, surf: pygame.Surface):
        y = 80
        opts = [("INTERFACE", self.selected_iface), ("ATTACK_WPS", self.opt_wps), ("ATTACK_WPA", self.opt_wpa), ("ATTACK_PMKID", self.opt_pmkid), ("PIXIE_DUST", self.opt_pixie), ("KILL_CONFL", self.opt_kill)]
        for i, (lbl, val) in enumerate(opts):
            sel = i == self.cursor
            rect = pygame.Rect(50, y + i*45, 450, 38)
            if sel: pygame.draw.rect(surf, (30, 30, 60), rect, border_radius=4)
            surf.blit(self.f_med.render(f"{lbl}:", True, theme.FG_DIM), (70, rect.y + 10))
            surf.blit(self.f_med.render(str(val), True, theme.ACCENT if sel else theme.FG), (250, rect.y + 10))

    def _render_scanning(self, surf: pygame.Surface):
        self._scan_y = (self._scan_y + 5) % 300
        pygame.draw.line(surf, (0, 255, 200, 100), (0, 100+self._scan_y), (theme.SCREEN_W, 100+self._scan_y), 2)
        term_rect = pygame.Rect(20, 100, theme.SCREEN_W-40, 300)
        pygame.draw.rect(surf, (0,0,0,150), term_rect, border_radius=8)
        for i, line in enumerate(list(self.history)[-12:]):
            surf.blit(self.f_small.render(line[:90], True, theme.ACCENT), (term_rect.x+20, term_rect.y+20+i*22))

    def _render_targets(self, surf: pygame.Surface):
        list_rect = pygame.Rect(20, 110, theme.SCREEN_W-40, 300)
        pygame.draw.rect(surf, (10, 10, 20), list_rect, border_radius=8)
        if not self.targets:
            surf.blit(self.f_med.render("WAITING_FOR_SIGNAL_LOCK...", True, theme.FG_DIM), (40, 150))
            return
        visible = self.targets[self.target_scroll : self.target_scroll + 8]
        for i, t in enumerate(visible):
            sel = (self.target_scroll + i) == self.target_cursor
            ry = list_rect.y + 20 + i * 32
            if sel: pygame.draw.rect(surf, (40, 40, 80), (30, ry-2, list_rect.width-20, 28), border_radius=4)
            surf.blit(self.f_med.render(f"{t.ssid[:25]} [{t.bssid}]", True, theme.ACCENT if sel else theme.FG), (40, ry))

    def _render_attacking(self, surf: pygame.Surface):
        center_x = theme.SCREEN_W // 2
        t = self.targets[self.target_cursor]
        gh, gw = 100, 300
        gx, gy = center_x - gw//2, 100
        pygame.draw.rect(surf, (0,15,0), (gx, gy, gw, gh))
        pygame.draw.rect(surf, theme.ACCENT_DIM, (gx, gy, gw, gh), 1)
        if len(t.power_history) > 1:
            pts = []
            for i, p in enumerate(t.power_history):
                px = gx + (i * (gw/20))
                py = gy + gh - int((p + 100) * (gh/70))
                pts.append((px, max(gy+2, min(gy+gh-2, py))))
            pygame.draw.lines(surf, theme.ACCENT, False, pts, 2)
        surf.blit(self.f_tiny.render("SIGNAL_OSCILLOSCOPE", True, theme.ACCENT), (gx, gy-15))
        for i, line in enumerate(list(self.history)[-5:]):
            surf.blit(self.f_small.render(f"> {line[:90]}", True, theme.ACCENT), (40, 280+i*24))

    def _render_loot(self, surf: pygame.Surface):
        y = 80
        surf.blit(self.f_med.render("SECURED_HANDSHAKES:", True, theme.ACCENT), (40, y))
        list_rect = pygame.Rect(30, y+30, 740, 300)
        pygame.draw.rect(surf, (10, 15, 10), list_rect, border_radius=8)
        pygame.draw.rect(surf, (0, 100, 50), list_rect, 1, border_radius=8)
        if not self.loot_list:
            surf.blit(self.f_med.render("NO_LOOT_ARCHIVED", True, theme.FG_DIM), (50, y+60))
        for i, f in enumerate(self.loot_list[:10]):
            sel = i == self.loot_cursor
            if sel: pygame.draw.rect(surf, (20, 40, 20), (40, y+45+i*28, 720, 26), border_radius=4)
            surf.blit(self.f_small.render(f, True, (150, 255, 150) if sel else theme.FG), (55, y+50+i*28))
