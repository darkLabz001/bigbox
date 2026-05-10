"""ADS-B Aircraft Tracker — SDR view.

Uses dump1090 to receive and parse ADS-B signals from nearby aircraft.
Displays aircraft hex, flight, altitude, speed, and distance.
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.sdr import get_adsb
from bigbox.ui.section import SectionContext


@dataclass
class Aircraft:
    hex_code: str
    flight: str = ""
    alt: str = ""
    speed: str = ""
    lat: str = ""
    lon: str = ""
    last_seen: float = 0.0


class ADSBView:
    def __init__(self) -> None:
        self.dismissed = False
        self.status_msg = "Press A to start dump1090"
        self.running = False
        self.sdr = get_adsb()
        self.aircrafts: dict[str, Aircraft] = {}
        self.cursor = 0

    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._shutdown()
            return

        if ev.button is Button.A:
            if not self.running:
                if self.sdr.start():
                    self.running = True
                    self.status_msg = "Listening for aircraft..."
                    # Start reader thread
                    threading.Thread(target=self._reader, daemon=True).start()
                else:
                    self.status_msg = "Error: dump1090 not found"
            else:
                self.sdr.stop()
                self.running = False
                self.status_msg = "Stopped"

    def _reader(self) -> None:
        while self.running and not self.dismissed:
            line = self.sdr.read_line()
            if line:
                self._parse_line(line)
            else:
                time.sleep(0.1)

    def _shutdown(self) -> None:
        self.running = False
        self.sdr.stop()
        self.dismissed = True

    def _parse_line(self, line: str) -> None:
        # Simple SBS1/BaseStation parser
        # MSG,3,1,1,406A36,1,2023/05/10,12:00:00.000,2023/05/10,12:00:00.000,,35000,,,51.1,-0.1,,,0,0,0,0
        parts = line.split(",")
        if len(parts) < 15:
            return
        
        hex_code = parts[4]
        if not hex_code:
            return

        ac = self.aircrafts.get(hex_code, Aircraft(hex_code=hex_code))
        ac.last_seen = time.time()

        msg_type = parts[1]
        if msg_type == "1": # ID
            ac.flight = parts[10].strip()
        elif msg_type == "3": # Position
            ac.alt = parts[11]
            ac.lat = parts[14]
            ac.lon = parts[15]
        elif msg_type == "4": # Velocity
            ac.speed = parts[12]

        self.aircrafts[hex_code] = ac

    def update(self) -> None:
        if self.running:
            # Non-blocking read would be better, but sdr.read_line is blocking.
            # For now, let's assume we call it enough times.
            # In a real app, this should be in a thread.
            pass

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        # ... render logic similar to other views ...
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("SDR :: ADS-B TRACKER", True, theme.ACCENT), (20, 20))
        
        y = 60
        f_small = pygame.font.Font(None, 24)
        
        # Cleanup old aircraft
        now = time.time()
        self.aircrafts = {k: v for k, v in self.aircrafts.items() if now - v.last_seen < 60}
        
        sorted_ac = sorted(self.aircrafts.values(), key=lambda x: x.last_seen, reverse=True)
        
        for ac in sorted_ac[:10]:
            label = f"{ac.hex_code} | {ac.flight:8} | {ac.alt:>5}ft | {ac.speed:>3}kt"
            surf.blit(f_small.render(label, True, theme.FG), (20, y))
            y += 25

        if not sorted_ac:
            surf.blit(f_small.render("No aircraft detected yet.", True, theme.FG_DIM), (20, y))

        status_surf = f_small.render(self.status_msg, True, theme.ACCENT)
        surf.blit(status_surf, (20, theme.SCREEN_H - 40))
