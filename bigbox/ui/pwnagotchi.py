"""Pwnagotchi-style WPA handshake harvester.
Uses hcxdumptool on a monitor-mode interface to collect PMKIDs and handshakes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pygame

from bigbox import hardware, theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext

LOOT_DIR = Path.home() / "loot" / "handshakes"

class PwnagotchiView:
    def __init__(self) -> None:
        self.dismissed = False
        self.status_msg = "Checking hardware..."
        
        # Sprites
        self.sprites: dict[str, pygame.Surface] = {}
        self._load_sprites()
        
        self.current_mood = "sleep"
        self._t0 = time.time()
        self._hop_t = 0.0
        self._blink_until = 0.0
        
        self.iface: str | None = None
        self.mon_iface: str | None = None
        self._proc: subprocess.Popen | None = None
        self._stop = False
        
        # Stats
        self.pmkid = 0
        self.eapol = 0
        self.aps = 0
        self.clients = 0
        self.start_time = 0.0
        
        # Startup worker
        threading.Thread(target=self._startup, daemon=True).start()

    def _load_sprites(self):
        ROOT = Path(__file__).resolve().parents[2]
        SPR_DIR = ROOT / "assets" / "sprites" / "pwn"
        for m in ("intense", "excited", "calm", "alert", "sad", "happy", "look", "sleep"):
            p = SPR_DIR / f"{m}.png"
            if p.exists():
                img = pygame.image.load(str(p)).convert_alpha()
                # Scale for the main screen
                h = 140
                w = int(img.get_width() * (h / img.get_height()))
                self.sprites[m] = pygame.transform.smoothscale(img, (w, h))

    def _startup(self):
        missing = hardware.check_dependencies("hcxdumptool", "iw")
        if missing:
            self.status_msg = f"Missing: {', '.join(missing)}"
            self.current_mood = "sad"
            return

        # Prefer an Alfa/USB adapter
        self.iface = hardware.preferred_monitor_iface()
        if not self.iface:
            self.status_msg = "No monitor-capable adapter found"
            self.current_mood = "sad"
            return

        if not hardware.request_iface(self.iface):
            self.status_msg = f"{self.iface} is busy"
            self.current_mood = "sad"
            return

        self.status_msg = f"Enabling monitor on {self.iface}..."
        self.mon_iface = hardware.enable_monitor(self.iface)
        if not self.mon_iface:
            self.status_msg = "Monitor mode failed"
            self.current_mood = "sad"
            hardware.release_iface(self.iface)
            return

        LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pcap = LOOT_DIR / f"pwn_{ts}.pcapng"
        
        self.status_msg = "Starting harvester..."
        self.current_mood = "look"
        self.start_time = time.time()
        
        # hcxdumptool command
        # -F: disable status screen
        # --enable_status=1: periodic status lines to stdout
        cmd = ["sudo", "hcxdumptool", "-i", self.mon_iface, "-o", str(pcap), "--enable_status=1"]
        
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self._watch_output()
        except Exception as e:
            self.status_msg = f"Failed: {e}"
            self.current_mood = "sad"

    def _watch_output(self):
        if not self._proc or not self._proc.stdout: return
        for line in self._proc.stdout:
            if self._stop: break
            
            # [PMKID:1] [EAPOL:2] [AP:5] [CLIENT:3]
            m_pmkid = re.search(r"PMKID:(\d+)", line)
            m_eapol = re.search(r"EAPOL:(\d+)", line)
            m_aps = re.search(r"AP:(\d+)", line)
            m_clients = re.search(r"CLIENT:(\d+)", line)
            
            new_loot = False
            if m_pmkid:
                val = int(m_pmkid.group(1))
                if val > self.pmkid: new_loot = True
                self.pmkid = val
            if m_eapol:
                val = int(m_eapol.group(1))
                if val > self.eapol: new_loot = True
                self.eapol = val
            if m_aps: self.aps = int(m_aps.group(1))
            if m_clients: self.clients = int(m_clients.group(1))
            
            if new_loot:
                self.status_msg = "GOT LOOT!"
                self.current_mood = "happy"
                self._hop_t = time.time()
                # Achievement integration
                from bigbox import achievements
                achievements.report_handshake()
            elif self.aps > 0:
                self.status_msg = "Hunting..."
                # Alternating excited/intense like original
                now = time.time()
                self.current_mood = "excited" if int(now * 0.7) % 2 else "intense"
            else:
                self.status_msg = "Scanning..."
                self.current_mood = "calm"

    def _shutdown(self):
        self._stop = True
        if self._proc:
            self._proc.terminate()
            try: self._proc.wait(timeout=2)
            except: self._proc.kill()
        
        if self.mon_iface:
            hardware.ensure_wifi_managed(self.iface)
        if self.iface:
            hardware.release_iface(self.iface)
        
        self.dismissed = True

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            self._shutdown()

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        # Header
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("RECON :: PWNAGOTCHI", True, theme.ACCENT), (theme.PADDING, 8))
        
        # Face animation
        now = time.time()
        t = now - self._t0
        bob = math.sin(t * 2.6) * 4
        hop = -15 if (now - self._hop_t) < 0.4 else 0
        
        mood = self.current_mood
        # Blinking
        open_eyed = {"intense", "excited", "alert", "sad", "look", "calm"}
        if mood in open_eyed and (t % 3.0) < 0.16:
            mood = "sleep"

        face = self.sprites.get(mood, self.sprites.get("calm"))
        if face:
            rect = face.get_rect(center=(theme.SCREEN_W // 2, head_h + 100 + bob + hop))
            surf.blit(face, rect)
            
        # Stats
        f_med = pygame.font.Font(None, 28)
        f_small = pygame.font.Font(None, 22)
        
        sy = head_h + 180
        stats = [
            f"APs: {self.aps}   Clients: {self.clients}",
            f"PMKIDs: {self.pmkid}   Handshakes: {self.eapol}",
        ]
        for i, s in enumerate(stats):
            txt = f_med.render(s, True, theme.FG)
            surf.blit(txt, (theme.SCREEN_W // 2 - txt.get_width() // 2, sy + i * 30))
            
        # Status footer
        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        s_surf = f_small.render(self.status_msg, True, theme.ACCENT)
        surf.blit(s_surf, (theme.PADDING, theme.SCREEN_H - foot_h + 8))
        
        hint = f_small.render("B: Stop & Exit", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W - hint.get_width() - hint.get_width() - theme.PADDING, theme.SCREEN_H - foot_h + 8))
