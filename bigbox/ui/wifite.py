"""Wifite — Professional automated wireless auditor with high-fidelity HUD."""
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
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Dict

import pygame

from bigbox import theme, hardware, webhooks
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
LOOT_DIRS = ["hs", "/root/hs", "handshakes"]

@dataclass
class WifiteTarget:
    id: int
    ssid: str
    bssid: str
    channel: str
    encryption: str
    power: int
    clients: int
    is_wps: bool = False
    power_history: List[int] = field(default_factory=list)

class WifiteView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.history = deque(maxlen=250)
        self.status_msg = "CORE_IDLE"
        
        # Gamification
        self.points = 0
        self.level = 1
        self._load_stats()
        
        # UI dimensions
        self.f_main = pygame.font.Font(None, 22)
        self.f_title = pygame.font.Font(None, 34)
        self.f_bold = pygame.font.Font(None, 26)
        self.f_small = pygame.font.Font(None, 18)
        self.f_tiny = pygame.font.Font(None, 14)
        
        # Attack Options (Toggles)
        self.opt_5ghz = False
        self.opt_wps = True
        self.opt_wpa = True
        self.opt_pmkid = True
        self.opt_pixie = True
        self.opt_pow_threshold = 40
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
        self.loot_list: List[Path] = []
        self.loot_cursor = 0
        self.loot_scroll = 0
        
        # Process management
        self.master_fd = None
        self.slave_fd = None
        self.process = None
        self._stop_event = threading.Event()
        self._reader_thread = None
        
        self.config_cursor = 0
        self._frame_count = 0
        self._scan_line_y = 0

    def _load_stats(self):
        if os.path.exists(GAMIFICATION_PATH):
            try:
                with open(GAMIFICATION_PATH, "r") as f:
                    data = json.load(f)
                    self.points = data.get("total_points", 0)
                    self.level = data.get("level", 1)
            except: pass

    def _award_points(self, amount: int):
        self.points += amount
        if os.path.exists(GAMIFICATION_PATH):
            try:
                with open(GAMIFICATION_PATH, "r+") as f:
                    data = json.load(f)
                    data["total_points"] = data.get("total_points", 0) + amount
                    f.seek(0); json.dump(data, f, indent=4); f.truncate()
            except: pass
        self.status_msg = f"ENTITY_DATA_SECURED: +{amount} PTS"

    def _refresh_loot(self):
        self.loot_list = []
        for d_str in LOOT_DIRS:
            d = Path(d_str)
            if d.exists() and d.is_dir():
                files = [f for f in d.iterdir() if f.is_file() and f.suffix in (".cap", ".pcap", ".pcapng", ".csv", ".txt", ".json")]
                self.loot_list.extend(files)
        # Unique and sorted by mod time
        self.loot_list = sorted(list(set(self.loot_list)), key=lambda p: p.stat().st_mtime, reverse=True)

    def _get_full_args(self) -> List[str]:
        args = ["--dict", "/usr/share/wordlists/rockyou.txt"]
        if self.opt_5ghz: args.append("-5")
        if self.opt_wps: args.append("--wps")
        else: args.append("--no-wps")
        if self.opt_wpa: args.append("--wpa")
        if self.opt_pmkid: args.append("--pmkid")
        if self.opt_pixie: args.append("--pixie")
        if self.opt_pow_threshold > 0: args.extend(["-pow", str(self.opt_pow_threshold)])
        if self.custom_args: args.extend(self.custom_args.split())
        return args

    def _start_wifite(self):
        self.phase = PHASE_SCANNING
        self.history.clear()
        self.targets.clear()
        
        cmd = ["sudo", "wifite", "-i", self.selected_iface] + self._get_full_args()
        self.history.append(f"[INIT] EXECUTING: {' '.join(cmd)}")
        
        self.master_fd, self.slave_fd = pty.openpty()
        try:
            self.process = subprocess.Popen(
                cmd, preexec_fn=os.setsid,
                stdin=self.slave_fd, stdout=self.slave_fd, stderr=self.slave_fd,
                env=os.environ
            )
            self._stop_event.clear()
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            self.status_msg = "SCANNING_RECON_ACTIVE"
        except Exception as e:
            self.status_msg = f"LAUNCH_FAIL: {e}"

    def _read_output(self):
        # Regex to match wifite's target list rows
        # 1  SSID  BSSID  CH  ENCR  POWER  CLIENTS
        target_re = re.compile(r"^\s*(\d+)\s+(.*?)\s+([0-9A-F:]{17})\s+(\d+)\s+(\w+[\w+]*)\s+(-?\d+)\s+(\d+)", re.IGNORECASE)
        
        while not self._stop_event.is_set() and self.master_fd:
            r, _, _ = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 4096).decode("utf-8", "replace")
                    if data:
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        for line in clean_data.splitlines():
                            stripped = line.strip()
                            if not stripped: continue
                            self.history.append(stripped)
                            
                            m = target_re.match(stripped)
                            if m:
                                tid, ssid, bssid, ch, enc, pwr, clients = m.groups()
                                tid, pwr, clients = int(tid), int(pwr), int(clients)
                                found = False
                                with threading.Lock():
                                    for t in self.targets:
                                        if t.bssid == bssid:
                                            t.power = pwr
                                            t.clients = clients
                                            t.power_history.append(pwr)
                                            if len(t.power_history) > 30: t.power_history.pop(0)
                                            found = True
                                            break
                                    if not found:
                                        self.targets.append(WifiteTarget(tid, ssid.strip(), bssid.upper(), ch, enc, pwr, clients, False, [pwr]))

                            if "captured" in stripped.lower() or "cracked" in stripped.lower():
                                self._award_points(100)

                            if "select target" in stripped.lower() or "enter number" in stripped.lower():
                                if self.phase == PHASE_SCANNING:
                                    self.phase = PHASE_TARGETS
                                    self.status_msg = "SPECTRUM_LOCKED"
                except OSError: break

    def _send_input(self, text: str):
        if self.master_fd and text:
            os.write(self.master_fd, (text + "\n").encode())

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
                self.status_msg = "ARCHIVE_SYNCED"

        elif self.phase == PHASE_CONFIG:
            opts_count = 8
            if ev.button is Button.UP: self.config_cursor = (self.config_cursor - 1) % opts_count
            elif ev.button is Button.DOWN: self.config_cursor = (self.config_cursor + 1) % opts_count
            elif ev.button is Button.A: self._toggle_config(ctx)
            elif ev.button is Button.START: self.phase = PHASE_LANDING

        elif self.phase == PHASE_SCANNING:
            if ev.button in (Button.A, Button.START, Button.LL):
                if self.process:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                    self.phase = PHASE_TARGETS
                    self.status_msg = "MANUAL_INTERRUPT"

        elif self.phase == PHASE_TARGETS:
            if not self.targets: return
            if ev.button is Button.UP:
                self.target_cursor = (self.target_cursor - 1) % len(self.targets)
                if self.target_cursor < self.target_scroll: self.target_scroll = self.target_cursor
                if self.target_cursor == len(self.targets)-1: self.target_scroll = max(0, len(self.targets)-10)
            elif ev.button is Button.DOWN:
                self.target_cursor = (self.target_cursor + 1) % len(self.targets)
                if self.target_cursor >= self.target_scroll + 10: self.target_scroll = self.target_cursor - 9
                if self.target_cursor == 0: self.target_scroll = 0
            elif ev.button is Button.A:
                t = self.targets[self.target_cursor]
                self._send_input(str(t.id))
                self.phase = PHASE_ATTACKING
                self.status_msg = f"ENGAGING_{t.ssid or t.bssid}"

        elif self.phase == PHASE_LOOT:
            if not self.loot_list: return
            if ev.button is Button.UP:
                self.loot_cursor = (self.loot_cursor - 1) % len(self.loot_list)
            elif ev.button is Button.DOWN:
                self.loot_cursor = (self.loot_cursor + 1) % len(self.loot_list)
            elif ev.button is Button.X: # Share via webhook
                path = self.loot_list[self.loot_cursor]
                self.status_msg = "UPLOADING_ARCHIVE..."
                def _do_upload():
                    ok, msg = webhooks.send_file(str(path))
                    self.status_msg = f"UPLOAD: {msg}"
                threading.Thread(target=_do_upload, daemon=True).start()
            elif ev.button is Button.Y: # Delete
                path = self.loot_list[self.loot_cursor]
                try:
                    path.unlink()
                    self._refresh_loot()
                    self.status_msg = "ARCHIVE_PURGED"
                except Exception as e:
                    self.status_msg = f"DELETE_FAIL: {e}"

    def _toggle_config(self, ctx: App):
        if self.config_cursor == 0:
            active = hardware.list_wifi_clients() + hardware.list_monitor_ifaces()
            ifaces = sorted(list(set(["wlan0", "wlan1"] + active)))
            idx = (ifaces.index(self.selected_iface) + 1) % len(ifaces) if self.selected_iface in ifaces else 0
            self.selected_iface = ifaces[idx]
        elif self.config_cursor == 1: self.opt_5ghz = not self.opt_5ghz
        elif self.config_cursor == 2: self.opt_wps = not self.opt_wps
        elif self.config_cursor == 3: self.opt_wpa = not self.opt_wpa
        elif self.config_cursor == 4: self.opt_pmkid = not self.opt_pmkid
        elif self.config_cursor == 5: self.opt_pixie = not self.opt_pixie
        elif self.config_cursor == 6:
            def _set_pow(v):
                try: self.opt_pow_threshold = int(v or 0)
                except: pass
            ctx.get_input("MIN_POWER_DB", _set_pow, str(self.opt_pow_threshold))
        elif self.config_cursor == 7:
            ctx.get_input("CUSTOM_PARAMS", lambda v: setattr(self, "custom_args", v or ""), self.custom_args)

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        self._frame_count += 1
        self._draw_hud_frame(surf)
        
        # Header
        head_h = 65
        surf.blit(self.f_title.render("WIFITE // NEURAL_AUDITOR_v2", True, theme.FG), (theme.PADDING + 10, 15))
        
        # Stats
        stats_x = 450
        surf.blit(self.f_tiny.render(f"ENTITY_LEVEL: {self.level:02d}", True, theme.ACCENT), (stats_x, 15))
        surf.blit(self.f_tiny.render(f"ENTITY_DATA:  {self.points:06d} EXP", True, theme.FG_DIM), (stats_x, 30))

        if self.phase == PHASE_LANDING: self._render_landing(surf, head_h)
        elif self.phase == PHASE_CONFIG: self._render_config(surf, head_h)
        elif self.phase == PHASE_SCANNING: self._render_scanning(surf, head_h)
        elif self.phase == PHASE_TARGETS: self._render_targets(surf, head_h)
        elif self.phase == PHASE_ATTACKING: self._render_attacking(surf, head_h)
        elif self.phase == PHASE_LOOT: self._render_loot(surf, head_h)

        # Footer
        foot_h = 32
        pygame.draw.line(surf, theme.DIVIDER, (20, theme.SCREEN_H - foot_h), (theme.SCREEN_W - 20, theme.SCREEN_H - foot_h))
        status_col = theme.ACCENT if self.process else theme.WARN
        surf.blit(self.f_small.render(f"KERNEL_STATE: {self.status_msg}", True, status_col), (25, theme.SCREEN_H - 25))
        
        hint = self._get_hint()
        h_surf = self.f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 25, theme.SCREEN_H - 25))

    def _draw_hud_frame(self, surf: pygame.Surface):
        color = theme.ACCENT
        bw, bh = 30, 30
        thickness = 2
        pygame.draw.lines(surf, color, False, [(0, bh), (0, 0), (bw, 0)], thickness)
        pygame.draw.lines(surf, color, False, [(theme.SCREEN_W-bw, 0), (theme.SCREEN_W-1, 0), (theme.SCREEN_W-1, bh)], thickness)
        pygame.draw.lines(surf, color, False, [(0, theme.SCREEN_H-bh), (0, theme.SCREEN_H-1), (bw, theme.SCREEN_H-1)], thickness)
        pygame.draw.lines(surf, color, False, [(theme.SCREEN_W-bw, theme.SCREEN_H-1), (theme.SCREEN_W-1, theme.SCREEN_H-1), (theme.SCREEN_W-1, theme.SCREEN_H-bh)], thickness)

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        bx, by = theme.SCREEN_W // 2 - 250, head_h + 40
        bw, bh = 500, 230
        pygame.draw.rect(surf, theme.BG_ALT, (bx, by, bw, bh), border_radius=4)
        pygame.draw.rect(surf, theme.DIVIDER, (bx, by, bw, bh), 1, border_radius=4)
        
        lines = [
            "WIFITE // AUTOMATED SPECTRUM AUDITOR",
            "MODES: WPA_HANDSHAKE / PMKID / WPS_PIXIE",
            "---------------------------------------",
            f"PRIMARY_INTERFACE: {self.selected_iface}",
            f"AUDIT_ARCHIVE:     {len(self.loot_list)} ENTRIES",
            "---------------------------------------",
            ">> PRESS A TO INITIALIZE SPECTRUM SCAN",
            ">> PRESS X TO RECONFIGURE PARAMETERS"
        ]
        for i, ln in enumerate(lines):
            col = theme.ACCENT if ">>" in ln else theme.FG
            font = self.f_bold if "WIFITE" in ln else self.f_main
            surf.blit(font.render(ln, True, col), (bx + 40, by + 30 + i * 25))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        y = head_h + 15
        surf.blit(self.f_bold.render("AUDIT_PARAMETER_CONFIGURATION", True, theme.ACCENT), (50, y))
        opts = [
            ("INTERFACE", self.selected_iface),
            ("SCAN_5GHZ", self.opt_5ghz),
            ("ATTACK_WPS", self.opt_wps),
            ("ATTACK_WPA", self.opt_wpa),
            ("CAPTURE_PMKID", self.opt_pmkid),
            ("WPS_PIXIE_DUST", self.opt_pixie),
            ("MIN_POWER_DB", f"-{self.opt_pow_threshold}"),
            ("CUSTOM_PARAMS", self.custom_args or "NONE")
        ]
        for i, (lbl, val) in enumerate(opts):
            sel = i == self.config_cursor
            rect = pygame.Rect(50, y + 40 + i*35, 450, 30)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=2)
                pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=2)
            surf.blit(self.f_main.render(f"{lbl}:", True, theme.FG_DIM), (70, rect.y + 5))
            surf.blit(self.f_main.render(str(val), True, theme.ACCENT if sel else theme.FG), (250, rect.y + 5))

    def _render_scanning(self, surf: pygame.Surface, head_h: int):
        self._scan_line_y = (self._scan_line_y + 4) % (theme.SCREEN_H - head_h - 60)
        pygame.draw.line(surf, (40, 120, 100), (20, head_h + 10 + self._scan_line_y), (theme.SCREEN_W - 20, head_h + 10 + self._scan_line_y), 1)
        
        log_rect = pygame.Rect(20, head_h + 10, theme.SCREEN_W - 40, theme.SCREEN_H - head_h - 60)
        pygame.draw.rect(surf, (5, 6, 10), log_rect, border_radius=2)
        pygame.draw.rect(surf, theme.DIVIDER, log_rect, 1)
        
        line_h = 18
        max_lines = log_rect.height // line_h - 1
        visible = list(self.history)[-max_lines:]
        for i, line in enumerate(visible):
            col = theme.ACCENT if "BSSID" in line or "SSID" in line else theme.FG
            surf.blit(self.f_tiny.render(line[:110], True, col), (log_rect.x + 10, log_rect.y + 8 + i * line_h))

    def _render_targets(self, surf: pygame.Surface, head_h: int):
        box = pygame.Rect(20, head_h + 10, 760, theme.SCREEN_H - head_h - 60)
        pygame.draw.rect(surf, theme.BG_ALT, box, border_radius=4)
        pygame.draw.rect(surf, theme.DIVIDER, box, 1)
        
        if not self.targets:
            surf.blit(self.f_main.render("WAITING_FOR_SIGNAL_LOCK...", True, theme.FG_DIM), (box.centerx - 100, box.centery))
            return

        headers = [("ID", 40), ("SSID_IDENTIFIER", 200), ("BSSID", 150), ("CH", 40), ("ENCR", 80), ("PWR", 60), ("CLNT", 50)]
        hx = box.x + 15
        for name, width in headers:
            surf.blit(self.f_tiny.render(name, True, theme.ACCENT), (hx, box.y + 10))
            hx += width
        pygame.draw.line(surf, theme.DIVIDER, (box.x+10, box.y+28), (box.right-10, box.y+28))

        row_h = 24
        visible = self.targets[self.target_scroll : self.target_scroll + 12]
        for i, t in enumerate(visible):
            idx = self.target_scroll + i
            sel = idx == self.target_cursor
            ry = box.y + 35 + i * row_h
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, (box.x+5, ry-2, box.width-10, row_h), border_radius=2)
                pygame.draw.rect(surf, theme.ACCENT, (box.x+5, ry-2, box.width-10, row_h), 1, border_radius=2)
            
            tx = box.x + 15
            surf.blit(self.f_small.render(str(t.id), True, theme.FG), (tx, ry))
            tx += 40
            surf.blit(self.f_small.render(t.ssid[:24], True, theme.FG), (tx, ry))
            tx += 200
            surf.blit(self.f_small.render(t.bssid, True, theme.FG_DIM), (tx, ry))
            tx += 150
            surf.blit(self.f_small.render(t.channel, True, theme.FG), (tx, ry))
            tx += 40
            surf.blit(self.f_small.render(t.encryption, True, theme.FG_DIM), (tx, ry))
            tx += 80
            p_col = theme.ACCENT if t.power > -60 else theme.WARN if t.power > -80 else theme.ERR
            surf.blit(self.f_small.render(f"{t.power}dB", True, p_col), (tx, ry))
            tx += 60
            surf.blit(self.f_small.render(str(t.clients), True, theme.FG), (tx, ry))

    def _render_attacking(self, surf: pygame.Surface, head_h: int):
        # Top info
        t = self.targets[self.target_cursor]
        info_rect = pygame.Rect(20, head_h + 10, theme.SCREEN_W - 40, 80)
        pygame.draw.rect(surf, theme.BG_ALT, info_rect, border_radius=4)
        pygame.draw.rect(surf, theme.ACCENT, info_rect, 1, border_radius=4)
        
        surf.blit(self.f_bold.render(f"ENGAGEMENT_TARGET: {t.ssid or t.bssid}", True, theme.ACCENT), (info_rect.x+20, info_rect.y+15))
        surf.blit(self.f_main.render(f"BSSID: {t.bssid}  |  CHAN: {t.channel}  |  ENCR: {t.encryption}  |  SIGNAL: {t.power}dB", True, theme.FG), (info_rect.x+20, info_rect.y+45))

        # Real-time Signal Oscilloscope
        osc_rect = pygame.Rect(20, info_rect.bottom + 15, 300, 120)
        pygame.draw.rect(surf, (5, 10, 8), osc_rect)
        pygame.draw.rect(surf, theme.DIVIDER, osc_rect, 1)
        surf.blit(self.f_tiny.render("SIGNAL_OSCILLOSCOPE", True, theme.ACCENT), (osc_rect.x+5, osc_rect.y-15))
        
        if len(t.power_history) > 1:
            pts = []
            for i, p in enumerate(t.power_history):
                px = osc_rect.x + (i * (osc_rect.width / 30))
                # Map -100..-20 to 0..height
                py = osc_rect.bottom - int((p + 100) * (osc_rect.height / 80))
                pts.append((px, max(osc_rect.y+2, min(osc_rect.bottom-2, py))))
            pygame.draw.lines(surf, theme.ACCENT, False, pts, 2)

        # Log tail
        log_rect = pygame.Rect(20, osc_rect.bottom + 15, theme.SCREEN_W - 40, theme.SCREEN_H - osc_rect.bottom - 60)
        pygame.draw.rect(surf, (5, 6, 10), log_rect, border_radius=2)
        pygame.draw.rect(surf, theme.DIVIDER, log_rect, 1)
        visible = list(self.history)[-8:]
        for i, line in enumerate(visible):
            surf.blit(self.f_tiny.render(f"> {line[:110]}", True, theme.FG), (log_rect.x+10, log_rect.y+8+i*18))

    def _render_loot(self, surf: pygame.Surface, head_h: int):
        box = pygame.Rect(20, head_h + 10, 760, theme.SCREEN_H - head_h - 60)
        pygame.draw.rect(surf, theme.BG_ALT, box, border_radius=4)
        pygame.draw.rect(surf, theme.DIVIDER, box, 1)
        
        surf.blit(self.f_bold.render("SECURED_AUDIT_ARCHIVES", True, theme.ACCENT), (box.x + 20, box.y + 15))
        
        if not self.loot_list:
            surf.blit(self.f_main.render("NO_LOOT_IDENTIFIED_IN_LOCAL_STORAGE", True, theme.FG_DIM), (box.centerx - 180, box.centery))
            return

        row_h = 26
        visible_count = (box.height - 60) // row_h
        visible = self.loot_list[self.loot_scroll : self.loot_scroll + visible_count]
        
        for i, path in enumerate(visible):
            idx = self.loot_scroll + i
            sel = idx == self.loot_cursor
            ry = box.y + 50 + i * row_h
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, (box.x+10, ry-2, box.width-20, row_h), border_radius=2)
                pygame.draw.rect(surf, theme.ACCENT, (box.x+10, ry-2, box.width-20, row_h), 1, border_radius=2)
            
            # File info
            size = path.stat().st_size / 1024
            mtime = time.strftime('%Y-%m-%d %H:%M', time.localtime(path.stat().st_mtime))
            surf.blit(self.f_small.render(path.name, True, theme.FG if not sel else theme.ACCENT), (box.x + 20, ry))
            surf.blit(self.f_tiny.render(f"{size:.1f} KB  |  {mtime}", True, theme.FG_DIM), (box.right - 180, ry + 3))

        # Controls hint
        hint_y = box.bottom - 25
        surf.blit(self.f_tiny.render("X: SHARE_DISCORD  Y: PURGE_FILE  B: RETURN", True, theme.FG_DIM), (box.x + 20, hint_y))
