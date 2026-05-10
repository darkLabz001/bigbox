"""Mission Report — Session summary and loot aggregator.

Provides a high-level overview of everything captured during the current
session/day. Generates a summary for display and mobile export.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from datetime import datetime

import pygame

from bigbox import theme, qr
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


class MissionReportView:
    def __init__(self) -> None:
        self.dismissed = False
        self.summary = self._generate_summary()
        self.show_qr = False
        self.qr_surf: pygame.Surface | None = None

        self.f_title = pygame.font.Font(None, 36)
        self.f_main = pygame.font.Font(None, 24)
        self.f_small = pygame.font.Font(None, 18)

    def _generate_summary(self) -> dict:
        stats = {
            "handshakes": 0,
            "creds": 0,
            "wardrive_nodes": 0,
            "trackers": 0,
            "scanned_devs": 0,
        }
        
        # 1. Handshakes
        h_dir = Path("loot/handshakes")
        if h_dir.exists():
            stats["handshakes"] = len(list(h_dir.glob("*.cap"))) + len(list(h_dir.glob("*.hc22000")))

        # 2. Creds (Captive Portal)
        c_dir = Path("loot/captive")
        if c_dir.exists():
            for f in c_dir.glob("*.csv"):
                try:
                    with f.open() as f_obj:
                        stats["creds"] += sum(1 for line in f_obj) - 1 # minus header
                except: pass

        # 3. Wardrive
        w_dir = Path("loot/wardrive")
        if w_dir.exists():
            for f in w_dir.glob("*.csv"):
                try:
                    with f.open() as f_obj:
                        stats["wardrive_nodes"] += sum(1 for line in f_obj) - 1
                except: pass

        # 4. Trackers
        t_path = Path("loot/tracker_history.jsonl")
        if t_path.exists():
            try:
                with t_path.open() as f_obj:
                    stats["trackers"] = sum(1 for line in f_obj)
            except: pass

        return stats

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed: return
        if ev.button is Button.B:
            if self.show_qr:
                self.show_qr = False
            else:
                self.dismissed = True
            return
        
        if ev.button is Button.X and not self.show_qr:
            # Generate QR
            text = f"BigBox Mission Report - {datetime.now().strftime('%Y-%m-%d')}\n"
            text += f"Handshakes: {self.summary['handshakes']}\n"
            text += f"Creds: {self.summary['creds']}\n"
            text += f"Nodes: {self.summary['wardrive_nodes']}\n"
            text += f"Trackers: {self.summary['trackers']}"
            self.qr_surf = qr.generate_surface(text, size=300)
            self.show_qr = True

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        
        title = self.f_title.render("MISSION DEBRIEF", True, theme.ACCENT)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2, 30))
        
        if self.show_qr and self.qr_surf:
            surf.blit(self.qr_surf, (theme.SCREEN_W // 2 - 150, 80))
            msg = self.f_main.render("SCAN TO EXPORT SUMMARY", True, theme.ACCENT)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, 400))
        else:
            y = 100
            lines = [
                ("WPA HANDSHAKES CAPTURED:", str(self.summary["handshakes"])),
                ("PORTAL CREDENTIALS LOGGED:", str(self.summary["creds"])),
                ("WARDRIVE NODES RECORDED:", str(self.summary["wardrive_nodes"])),
                ("BLUETOOTH TRACKER SIGHTINGS:", str(self.summary["trackers"])),
            ]
            
            for lbl, val in lines:
                surf.blit(self.f_main.render(lbl, True, theme.FG_DIM), (100, y))
                surf.blit(self.f_title.render(val, True, theme.ACCENT), (550, y - 5))
                y += 60
                
            footer = self.f_main.render("MISSION DATA SAVED TO LOOT/", True, theme.FG_DIM)
            surf.blit(footer, (theme.SCREEN_W // 2 - footer.get_width() // 2, 360))

        hint = self.f_small.render("X: GENERATE QR EXPORT  B: BACK", True, theme.FG_DIM)
        if self.show_qr: hint = self.f_small.render("B: CLOSE QR", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W // 2 - hint.get_width() // 2, theme.SCREEN_H - 30))
