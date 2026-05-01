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
from collections import deque
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
        
        self.process = None
        self.master_fd = None
        self.slave_fd = None
        self._stop_event = threading.Event()
        self.history = deque(maxlen=200)
        
        self.pcapng_path: Path | None = None
        self.pmkid_count = 0
        self.eapol_count = 0
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
                    # Run in thread to not block UI
                    threading.Thread(target=self._do_upload, daemon=True).start()
            elif ev.button is Button.B:
                self._stop_snipe()
                self.dismissed = True

        elif self.phase == PHASE_RESULT:
            if ev.button in (Button.A, Button.B):
                self.phase = PHASE_PICK_IFACE

    def _do_upload(self):
        success = hashopolis.upload_hash(self.pcapng_path)
        self.status_msg = "UPLOAD SUCCESS" if success else "UPLOAD FAILED"

    def _start_snipe(self) -> None:
        self.LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.pcapng_path = self.LOOT_DIR / f"sniper_{ts}.pcapng"
        
        # hcxdumptool command
        # -i interface, -o output pcapng, --enable_status=1 for console output
        cmd = [
            "sudo", "hcxdumptool",
            "-i", self.mon_iface,
            "-o", str(self.pcapng_path),
            "--enable_status=1"
        ]
        
        self.phase = PHASE_SNIPING
        self.status_msg = "SNIPING ACTIVE"
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
            threading.Thread(target=self._read_output, daemon=True).start()
            from bigbox import background as _bg
            _bg.register(
                "pmkid_sniper",
                f"PMKID sniper ({self.mon_iface})",
                "Wireless",
                stop=self._stop_snipe,
            )
        except Exception as e:
            self.status_msg = f"ERR: {str(e)}"
            self.phase = PHASE_RESULT

    def _read_output(self) -> None:
        while not self._stop_event.is_set() and self.master_fd:
            r, _, _ = select.select([self.master_fd], [], [], 0.1)
            if self.master_fd in r:
                try:
                    data = os.read(self.master_fd, 4096).decode("utf-8", "replace")
                    if data:
                        # Strip ANSI escape codes
                        clean_data = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', data)
                        for line in clean_data.splitlines():
                            line = line.strip()
                            if not line: continue
                            self.history.append(line)
                            
                            # Parse status from hcxdumptool
                            # Example lines often contain [PMKID: 5] or [EAPOL: 2]
                            m_pmkid = re.search(r'PMKID:?\s*(\d+)', line)
                            if m_pmkid: self.pmkid_count = int(m_pmkid.group(1))
                            
                            m_eapol = re.search(r'EAPOL:?\s*(\d+)', line)
                            if m_eapol: self.eapol_count = int(m_eapol.group(1))
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
        from bigbox import background as _bg
        _bg.unregister("pmkid_sniper")
        self.phase = PHASE_RESULT

    def update(self) -> None:
        pass

    def render(self, surf: pygame.Surface) -> None:
        # Fonts here use the standard pygame.font.Font(None, N) form so
        # the bigbox._font_cache monkey-patch can dedupe instances —
        # don't reach back into App for a shared dict.
        f = pygame.font.Font(None, theme.FS_BODY)
        f_bold = pygame.font.Font(None, theme.FS_TITLE)
        f_small = pygame.font.Font(None, theme.FS_SMALL)
        f_tiny = f_small
        
        head_h, foot_h = 40, 32
        surf.fill(theme.BG)
        
        # Header
        pygame.draw.rect(surf, theme.FG, (0, 0, surf.get_width(), head_h))
        title = f_bold.render("PMKID SNIPER", True, theme.BG)
        surf.blit(title, (10, (head_h - title.get_height()) // 2))
        
        # Status Bar
        stat_surf = f_small.render(self.status_msg, True, theme.FG)
        surf.blit(stat_surf, (surf.get_width() - stat_surf.get_width() - 10, (head_h - stat_surf.get_height()) // 2))

        if self.phase == PHASE_PICK_IFACE:
            y = head_h + 20
            surf.blit(f.render("SELECT INTERFACE:", True, theme.ACCENT), (20, y))
            y += 30
            for i, iface in enumerate(self.ifaces):
                color = theme.FG if i == self.iface_idx else theme.FG_DIM
                txt = f"{'> ' if i == self.iface_idx else '  '}{iface.name} {'(mon)' if iface.is_monitor else ''}"
                surf.blit(f.render(txt, True, color), (20, y + i*30))
        
        elif self.phase == PHASE_SNIPING:
            # Stats Bar
            stats_y = head_h + 5
            elapsed = int(time.time() - self.start_time)
            stats_txt = f"T: {elapsed}s | PMKIDs: {self.pmkid_count} | EAPOLs: {self.eapol_count}"
            surf.blit(f.render(stats_txt, True, theme.ACCENT), (20, stats_y))
            
            # Terminal Area
            term_rect = pygame.Rect(10, head_h + 30, surf.get_width() - 20, surf.get_height() - head_h - foot_h - 40)
            pygame.draw.rect(surf, (5, 5, 5), term_rect)
            pygame.draw.rect(surf, theme.DIVIDER, term_rect, 1)
            
            visible_lines = list(self.history)[-(term_rect.height // 16):]
            for i, line in enumerate(visible_lines):
                surf.blit(f_tiny.render(line[:80], True, theme.FG), (term_rect.x + 5, term_rect.y + 5 + i * 16))
            
            # Hints
            hint_txt = "A: Stop  X: Hashopolis Upload  B: Back"
            surf.blit(f_small.render(hint_txt, True, theme.FG_DIM), (20, surf.get_height() - 25))

        elif self.phase == PHASE_RESULT:
            surf.blit(f.render("SNIPING COMPLETE", True, theme.FG), (20, head_h + 20))
            if self.pcapng_path:
                surf.blit(f_small.render(f"Saved: {self.pcapng_path.name}", True, theme.FG_DIM), (20, head_h + 50))
                surf.blit(f.render(f"Total PMKIDs: {self.pmkid_count}", True, theme.ACCENT), (20, head_h + 80))
            surf.blit(f.render("A: Back", True, theme.FG), (20, head_h + 120))
