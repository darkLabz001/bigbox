"""PMKID Sniper tool — uses hcxdumptool for silent credential harvesting.

This tool captures PMKIDs and handshakes without necessarily deauthing,
by requesting them directly from the AP. It is the modern gold standard
for WiFi pentesting.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import pty
import select
import time
import shutil
import math
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional
from dataclasses import dataclass

import pygame

from bigbox import theme, hardware, hashopolis
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App

PHASE_LANDING = "landing"
PHASE_CONFIG = "config"
PHASE_SNIPING = "sniping"
PHASE_RESULT = "result"

@dataclass
class _Iface:
    name: str
    is_monitor: bool

class PMKIDSniperView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.status_msg = "CORE_IDLE"
        
        # Hardware
        self.ifaces: List[_Iface] = []
        self._refresh_ifaces()
        self.iface_idx = 0
        self.mon_iface: str | None = None
        
        # Options
        self.opt_hop = True
        self.opt_channel = 6
        self.opt_aggressive = False
        self.opt_bpf = ""
        self.opt_wait = 5 # seconds to wait for PMKID per AP
        
        # Process management
        self.process = None
        self.master_fd = None
        self.slave_fd = None
        self._stop_event = threading.Event()
        self.history = deque(maxlen=250)
        
        # Stats
        self.pcapng_path: Path | None = None
        self.pmkid_count = 0
        self.eapol_count = 0
        self.start_time = 0.0
        self.last_capture_time = 0.0
        
        # UI
        self.config_cursor = 0
        self.f_main = pygame.font.Font(None, 24)
        self.f_title = pygame.font.Font(None, 34)
        self.f_bold = pygame.font.Font(None, 26)
        self.f_small = pygame.font.Font(None, 18)
        self.f_tiny = pygame.font.Font(None, 14)
        
        self.LOOT_DIR = Path("loot/handshakes")
        self._frame_count = 0

    def _refresh_ifaces(self):
        self.ifaces = []
        # Get all wifi ifaces
        clients = hardware.list_wifi_clients()
        mons = hardware.list_monitor_ifaces()
        
        seen = set()
        for m in mons:
            self.ifaces.append(_Iface(m, True))
            seen.add(m)
        for c in clients:
            if c not in seen:
                self.ifaces.append(_Iface(c, False))
        
        if not self.ifaces:
            self.ifaces = [_Iface("wlan0", False)]

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed: return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.UP:
                self.iface_idx = (self.iface_idx - 1) % len(self.ifaces)
            elif ev.button is Button.DOWN:
                self.iface_idx = (self.iface_idx + 1) % len(self.ifaces)
            elif ev.button is Button.A:
                self.mon_iface = self.ifaces[self.iface_idx].name
                self.phase = PHASE_CONFIG
            elif ev.button is Button.X:
                self._refresh_ifaces()
            elif ev.button is Button.B:
                self.dismissed = True

        elif self.phase == PHASE_CONFIG:
            opts_count = 6
            if ev.button is Button.UP:
                self.config_cursor = (self.config_cursor - 1) % opts_count
            elif ev.button is Button.DOWN:
                self.config_cursor = (self.config_cursor + 1) % opts_count
            elif ev.button is Button.A:
                self._toggle_config(ctx)
            elif ev.button is Button.START:
                self._start_snipe()
            elif ev.button is Button.B:
                self.phase = PHASE_LANDING

        elif self.phase == PHASE_SNIPING:
            if ev.button is Button.A: # Stop
                self._stop_snipe()
            elif ev.button is Button.X: # Upload
                if self.pcapng_path and self.pcapng_path.exists():
                    self.status_msg = "UPLOADING_TO_HASHOPOLIS..."
                    threading.Thread(target=self._do_upload, daemon=True).start()
            elif ev.button is Button.B:
                self._stop_snipe()
                self.phase = PHASE_LANDING

        elif self.phase == PHASE_RESULT:
            if ev.button in (Button.A, Button.B, Button.START):
                self.phase = PHASE_LANDING

    def _toggle_config(self, ctx: App):
        if self.config_cursor == 0:
            self.opt_hop = not self.opt_hop
        elif self.config_cursor == 1:
            ctx.get_input("CHANNEL", lambda v: setattr(self, "opt_channel", int(v or 6)), str(self.opt_channel))
        elif self.config_cursor == 2:
            self.opt_aggressive = not self.opt_aggressive
        elif self.config_cursor == 3:
            ctx.get_input("WAIT_TIME", lambda v: setattr(self, "opt_wait", int(v or 5)), str(self.opt_wait))
        elif self.config_cursor == 4:
            ctx.get_input("BPF_FILTER", lambda v: setattr(self, "opt_bpf", v or ""), self.opt_bpf)
        elif self.config_cursor == 5:
            self._start_snipe()

    def _do_upload(self):
        success = hashopolis.upload_hash(self.pcapng_path)
        self.status_msg = "UPLOAD_SUCCESS" if success else "UPLOAD_FAILED"

    def _start_snipe(self) -> None:
        if not shutil.which("hcxdumptool"):
            self.status_msg = "ERR: HCXDUMPTOOL_MISSING"
            self.phase = PHASE_RESULT
            return

        # 1. Ensure monitor mode
        mon_ifaces = hardware.list_monitor_ifaces()
        active_iface = self.mon_iface
        if active_iface not in mon_ifaces:
            self.status_msg = f"ENABLING_MONITOR_{active_iface}..."
            new_mon = hardware.enable_monitor(active_iface)
            if not new_mon:
                self.status_msg = "ERR: MONITOR_MODE_FAILED"
                self.phase = PHASE_LANDING
                return
            active_iface = new_mon
            self.mon_iface = active_iface

        self.LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.pcapng_path = self.LOOT_DIR / f"sniper_{ts}.pcapng"
        
        # Build command (hcxdumptool v6.x uses -w instead of -o)
        # bigbox runs as root; drop the sudo prefix.
        cmd = ["hcxdumptool", "-i", active_iface, "-w", str(self.pcapng_path)]

        if not self.opt_hop:
            # Try to set channel via iw
            subprocess.run(["iw", "dev", active_iface, "set", "channel", str(self.opt_channel)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        self.phase = PHASE_SNIPING
        self.status_msg = "SNIPING_ACTIVE"
        self.start_time = time.time()
        self.pmkid_count = 0
        self.eapol_count = 0
        self.history.clear()
        self._stop_event.clear()

        self.master_fd, self.slave_fd = pty.openpty()
        try:
            self.process = subprocess.Popen(
                cmd, preexec_fn=os.setsid,
                stdin=self.slave_fd, stdout=self.slave_fd, stderr=self.slave_fd,
                env=os.environ
            )
            self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
            self._reader_thread.start()
            from bigbox import background as _bg
            _bg.register(
                "pmkid_sniper",
                f"PMKID sniper ({active_iface})",
                "Wireless",
                stop=self._stop_snipe,
            )
        except Exception as e:
            self.status_msg = f"LAUNCH_FAIL: {e}"
            self.phase = PHASE_RESULT

    def _read_output(self) -> None:
        while not self._stop_event.is_set() and self.master_fd:
            r, _, _ = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 4096).decode("utf-8", "replace")
                    if data:
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        for line in clean_data.splitlines():
                            line = line.strip()
                            if not line: continue
                            self.history.append(line)
                            
                            m_pmkid = re.search(r'PMKID:?\s*(\d+)', line, re.I)
                            if m_pmkid: 
                                new_count = int(m_pmkid.group(1))
                                if new_count > self.pmkid_count:
                                    self.pmkid_count = new_count
                                    self.last_capture_time = time.time()
                            
                            m_eapol = re.search(r'EAPOL:?\s*(\d+)', line, re.I)
                            if m_eapol: 
                                new_e = int(m_eapol.group(1))
                                if new_e > self.eapol_count:
                                    self.eapol_count = new_e
                                    self.last_capture_time = time.time()
                except OSError: break

    def _stop_snipe(self) -> None:
        self._stop_event.set()
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGINT)
                time.sleep(1)
                if self.process.poll() is None:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            except: pass
        if self.master_fd:
            try: os.close(self.master_fd)
            except: pass
        if self.slave_fd:
            try: os.close(self.slave_fd)
            except: pass
        self.master_fd = self.slave_fd = self.process = None
        hardware.ensure_wifi_managed(self.mon_iface)
        from bigbox import background as _bg
        _bg.unregister("pmkid_sniper")
        if self.phase == PHASE_SNIPING:
            self.phase = PHASE_RESULT
            # Parse the .pcapng into a list of (kind, BSSID, ESSID)
            # rows so the result screen shows what was actually
            # captured, not just counters. Runs in a background thread
            # because hcxpcapngtool can take a few seconds on large
            # captures and we don't want to block the UI.
            self.parsed_captures = []
            if self.pcapng_path and self.pcapng_path.is_file():
                threading.Thread(target=self._parse_captures, daemon=True).start()

    def _parse_captures(self) -> None:
        try:
            from bigbox import pcap_parse
            self.parsed_captures = pcap_parse.parse_pcapng(self.pcapng_path)
        except Exception as e:
            print(f"[pmkid] parse failed: {e}")
            self.parsed_captures = []

    def render(self, surf: pygame.Surface) -> None:
        self._frame_count += 1
        surf.fill(theme.BG)
        self._draw_hud_frame(surf)
        
        head_h = 65
        surf.blit(self.f_title.render("PMKID // NEURAL_SNIPER_v3", True, theme.FG), (theme.PADDING + 10, 15))
        
        if self.phase == PHASE_LANDING: self._render_landing(surf, head_h)
        elif self.phase == PHASE_CONFIG: self._render_config(surf, head_h)
        elif self.phase == PHASE_SNIPING: self._render_sniping(surf, head_h)
        elif self.phase == PHASE_RESULT: self._render_result(surf, head_h)

        # Footer
        foot_h = 32
        pygame.draw.line(surf, theme.DIVIDER, (20, theme.SCREEN_H - foot_h), (theme.SCREEN_W - 20, theme.SCREEN_H - foot_h))
        status_col = theme.ACCENT if self.process else theme.WARN
        surf.blit(self.f_small.render(f"STATION_STATUS: {self.status_msg}", True, status_col), (25, theme.SCREEN_H - 25))
        h_surf = self.f_small.render(self._get_hint(), True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - 25, theme.SCREEN_H - 25))

    def _get_hint(self) -> str:
        if self.phase == PHASE_LANDING: return "A: SELECT_IFACE  X: REFRESH  B: EXIT"
        if self.phase == PHASE_CONFIG: return "UP/DN: NAV  A: TOGGLE  START: INITIATE"
        if self.phase == PHASE_SNIPING: return "A: TERMINATE  X: UPLOAD  B: ABORT"
        if self.phase == PHASE_RESULT: return "A: RETURN  B: EXIT"
        return "B: BACK"

    def _draw_hud_frame(self, surf: pygame.Surface):
        color = theme.ACCENT
        bw, bh = 30, 30
        pygame.draw.lines(surf, color, False, [(0, bh), (0, 0), (bw, 0)], 2)
        pygame.draw.lines(surf, color, False, [(theme.SCREEN_W-bw, 0), (theme.SCREEN_W-1, 0), (theme.SCREEN_W-1, bh)], 2)
        pygame.draw.lines(surf, color, False, [(0, theme.SCREEN_H-bh), (0, theme.SCREEN_H-1), (bw, theme.SCREEN_H-1)], 2)
        pygame.draw.lines(surf, color, False, [(theme.SCREEN_W-bw, theme.SCREEN_H-1), (theme.SCREEN_W-1, theme.SCREEN_H-1), (theme.SCREEN_W-1, theme.SCREEN_H-bh)], 2)

    def _render_landing(self, surf: pygame.Surface, head_h: int):
        y = head_h + 20
        surf.blit(self.f_bold.render("AVAILABLE_TRANSCEIVERS", True, theme.ACCENT), (50, y))
        for i, iface in enumerate(self.ifaces):
            sel = i == self.iface_idx
            rect = pygame.Rect(50, y + 40 + i*45, 400, 40)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=4)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=4)
            color = theme.ACCENT if sel else theme.FG
            txt = f"{iface.name} {'(monitor_mode_active)' if iface.is_monitor else ''}"
            surf.blit(self.f_main.render(txt, True, color), (rect.x + 15, rect.y + 8))

    def _render_config(self, surf: pygame.Surface, head_h: int):
        y = head_h + 15
        surf.blit(self.f_bold.render("ENGAGEMENT_PARAMETERS", True, theme.ACCENT), (50, y))
        opts = [
            ("CHANNEL_HOPPING", "YES" if self.opt_hop else "NO"),
            ("FIXED_CHANNEL", str(self.opt_channel)),
            ("AGGRESSIVE_MODE", "YES" if self.opt_aggressive else "NO"),
            ("DWELL_TIME", f"{self.opt_wait}s"),
            ("BPF_FILTER", self.opt_bpf or "NONE"),
            (">> INITIALIZE SNIPER", "")
        ]
        for i, (lbl, val) in enumerate(opts):
            sel = i == self.config_cursor
            rect = pygame.Rect(50, y + 45 + i*38, 450, 32)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=2)
                pygame.draw.rect(surf, theme.ACCENT, rect, 1, border_radius=2)
            surf.blit(self.f_main.render(f"{lbl}:", True, theme.FG_DIM), (70, rect.y + 5))
            surf.blit(self.f_main.render(str(val), True, theme.ACCENT if sel else theme.FG), (280, rect.y + 5))

    def _render_sniping(self, surf: pygame.Surface, head_h: int):
        # Scan animation
        scan_y = (self._frame_count * 5) % (theme.SCREEN_H - head_h - 60)
        line_surf = pygame.Surface((theme.SCREEN_W - 40, 2), pygame.SRCALPHA)
        line_surf.fill((0, 255, 100, 100))
        surf.blit(line_surf, (20, head_h + 20 + scan_y))
        
        # Stats HUD
        stats_rect = pygame.Rect(20, head_h + 10, theme.SCREEN_W - 40, 90)
        pygame.draw.rect(surf, theme.BG_ALT, stats_rect, border_radius=5)
        pygame.draw.rect(surf, theme.ACCENT, stats_rect, 1, border_radius=5)
        
        elapsed = int(time.time() - self.start_time)
        surf.blit(self.f_bold.render(f"UPTIME: {elapsed}s", True, theme.FG), (stats_rect.x + 20, stats_rect.y + 15))
        
        # Capture counts with pulse effect
        pulse = abs(math.sin(time.time() * 5)) if (time.time() - self.last_capture_time < 2) else 0
        p_col = (0, 255, 0) if pulse > 0 else theme.ACCENT
        
        surf.blit(self.f_main.render(f"PMKIDs: {self.pmkid_count}", True, p_col), (stats_rect.x + 20, stats_rect.y + 50))
        surf.blit(self.f_main.render(f"EAPOLs: {self.eapol_count}", True, theme.WARN), (stats_rect.x + 200, stats_rect.y + 50))
        
        # Terminal log
        term_rect = pygame.Rect(20, stats_rect.bottom + 15, theme.SCREEN_W - 40, theme.SCREEN_H - stats_rect.bottom - 65)
        pygame.draw.rect(surf, (5, 5, 10), term_rect, border_radius=2)
        pygame.draw.rect(surf, theme.DIVIDER, term_rect, 1)
        
        lines = list(self.history)[-(term_rect.height // 18 - 1):]
        for i, line in enumerate(lines):
            col = theme.ACCENT if "PMKID" in line or "EAPOL" in line else theme.FG
            surf.blit(self.f_tiny.render(line[:120], True, col), (term_rect.x + 10, term_rect.y + 8 + i * 18))

    def _render_result(self, surf: pygame.Surface, head_h: int):
        box_w, box_h = 720, 360
        bx = (theme.SCREEN_W - box_w) // 2
        by = head_h + 20
        pygame.draw.rect(surf, theme.BG_ALT, (bx, by, box_w, box_h), border_radius=10)
        pygame.draw.rect(surf, theme.DIVIDER, (bx, by, box_w, box_h), 1, border_radius=10)

        surf.blit(self.f_bold.render("MISSION_COMPLETE", True, theme.ACCENT),
                  (bx + 30, by + 18))

        # Summary line — file + counters in one row.
        size_kb = (os.path.getsize(self.pcapng_path) // 1024
                   if self.pcapng_path and self.pcapng_path.exists() else 0)
        summary = (
            f"{self.pcapng_path.name if self.pcapng_path else 'NONE'}  ·  "
            f"{size_kb} KB  ·  PMKIDs: {self.pmkid_count}  EAPOLs: {self.eapol_count}"
        )
        surf.blit(self.f_small.render(summary, True, theme.FG_DIM),
                  (bx + 30, by + 50))

        # Inline capture list — populated by _parse_captures off the
        # main thread. Until it lands the user sees "parsing..."; once
        # parsed we list every (kind, BSSID, ESSID) row.
        rows = getattr(self, "parsed_captures", None)
        list_y = by + 80
        list_h = box_h - 100
        pygame.draw.rect(surf, (5, 5, 10),
                         (bx + 20, list_y, box_w - 40, list_h))
        pygame.draw.rect(surf, theme.DIVIDER,
                         (bx + 20, list_y, box_w - 40, list_h), 1)

        if rows is None:
            msg = self.f_small.render("Parsing pcapng...", True, theme.FG_DIM)
            surf.blit(msg, (bx + 30, list_y + 12))
        elif not rows:
            msg = self.f_small.render(
                "No PMKIDs / handshakes found in capture.",
                True, theme.FG_DIM)
            surf.blit(msg, (bx + 30, list_y + 12))
        else:
            header = self.f_small.render(
                f"{len(rows)} unique capture(s):", True, theme.ACCENT)
            surf.blit(header, (bx + 30, list_y + 8))
            row_h = 22
            visible = (list_h - 38) // row_h
            for i, cap in enumerate(rows[:visible]):
                color = theme.ACCENT if cap.kind == "PMKID" else theme.WARN
                line = (f"[{cap.kind:5}] {cap.bssid}  "
                        f"{cap.essid[:36]}")
                ls = self.f_small.render(line, True, color)
                surf.blit(ls, (bx + 30, list_y + 32 + i * row_h))
            if len(rows) > visible:
                more = self.f_small.render(
                    f"+ {len(rows) - visible} more in {self.pcapng_path.name}",
                    True, theme.FG_DIM)
                surf.blit(more, (bx + 30, list_y + 32 + visible * row_h))
