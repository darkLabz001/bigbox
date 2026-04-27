"""Unknown-tracker detector — UI view.

Live counts per tracker type, recent detections list, GPS strip, and a
red full-bleed alert banner when the heuristic in
bigbox/trackers.py:TrackerDetector.alerts() fires (a tracker type
visible across enough consecutive 1-min windows + GPS span).

Ergonomics match other Recon-style views: A toggles capture, B exits
with cleanup. Reuses the GPSReader the wardrive view depends on, so
movement-aware alerting just works.
"""
from __future__ import annotations

import time
from datetime import datetime

import pygame

from bigbox import hardware, theme
from bigbox.events import Button, ButtonEvent
from bigbox.gps import GPSReader
from bigbox.trackers import (
    ALERT_WINDOWS,
    Detection,
    TRACKER_TYPES,
    TrackerDetector,
    _by_key,
)
from bigbox.ui.section import SectionContext


PHASE_LANDING = "landing"
PHASE_SCANNING = "scanning"


class TrackerView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.status_msg = "Press A to start passive scan"

        # Recover BT controller in case a previous tool left it sideways.
        hardware.ensure_bluetooth_on()

        # GPS for movement-aware alerts.
        self.gps = GPSReader()
        self.gps.start()

        self.detector = TrackerDetector(self.gps)
        self._scan_started_at: float = 0.0

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._shutdown()
            return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                self.detector.start()
                if self.detector._proc is None:  # btmon missing
                    self.status_msg = "btmon not installed (apt install bluez)"
                    return
                self._scan_started_at = time.time()
                self.phase = PHASE_SCANNING
                self.status_msg = "Listening for tracker advertisements..."
            return

        if self.phase == PHASE_SCANNING:
            if ev.button is Button.A:
                # Stop -> back to landing
                self.detector.stop()
                self.phase = PHASE_LANDING
                self.status_msg = "Stopped"
            return

    def _shutdown(self) -> None:
        try:
            self.detector.stop()
        except Exception:
            pass
        try:
            self.gps.stop()
        except Exception:
            pass
        self.dismissed = True

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("BLUETOOTH :: TRACKER_HUNT", True, theme.ACCENT),
                  (theme.PADDING, 8))

        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20),
                         (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER,
                         (0, theme.SCREEN_H - foot_h),
                         (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        f_small = pygame.font.Font(None, 20)
        s_surf = f_small.render(self.status_msg[:60], True, theme.ACCENT)
        surf.blit(s_surf, (theme.PADDING, theme.SCREEN_H - foot_h + 8))
        h_surf = f_small.render(self._hint(), True, theme.FG_DIM)
        surf.blit(h_surf,
                  (theme.SCREEN_W - h_surf.get_width() - theme.PADDING,
                   theme.SCREEN_H - foot_h + 8))

        # GPS state strip — wardrive-style
        self._render_gps_strip(surf, head_h)

        if self.phase == PHASE_LANDING:
            self._render_landing(surf, head_h, foot_h)
        else:
            self._render_scanning(surf, head_h, foot_h)

    def _hint(self) -> str:
        if self.phase == PHASE_LANDING:
            return "A: Start  B: Back"
        return "A: Stop  B: Back"

    def _render_gps_strip(self, surf: pygame.Surface, head_h: int) -> None:
        fix = self.gps.latest()
        f = pygame.font.Font(None, 20)
        y = head_h + 6
        if not fix.device_path:
            label = "GPS: NO DEVICE — alerts are time-based only"
            color = theme.WARN
        elif not fix.has_fix:
            label = f"GPS: SEARCHING ({fix.device_path})"
            color = theme.WARN
        else:
            label = (f"GPS: {fix.lat:.4f}, {fix.lon:.4f}  "
                     f"{fix.speed_kmh:.0f} km/h  sats {fix.sats}")
            color = theme.ACCENT
        surf.blit(f.render(label, True, color), (theme.PADDING, y))

    def _render_landing(self, surf: pygame.Surface,
                        head_h: int, foot_h: int) -> None:
        f_big = pygame.font.Font(None, 38)
        f_med = pygame.font.Font(None, 22)
        msg = f_big.render("Tracker Detector", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                        head_h + 50))

        lines = [
            "Listens for AirTags / AirPods / SmartTags / Tile broadcasts",
            "and alerts if one stays in range across multiple GPS",
            "waypoints — i.e. likely following you, not just sitting in",
            "a building you walked past once.",
            "",
            "Apple's spec rotates the BLE MAC every ~15 min so we don't",
            "track a specific tracker — instead we track tracker presence",
            "while you've moved enough distance that a stationary one",
            "would have dropped out.",
            "",
            "Press A to start. B to exit.",
        ]
        for i, ln in enumerate(lines):
            color = theme.FG_DIM if not ln.startswith("Press") else theme.ACCENT
            ls = f_med.render(ln, True, color)
            surf.blit(ls, (theme.SCREEN_W // 2 - ls.get_width() // 2,
                           head_h + 110 + i * 24))

    def _render_scanning(self, surf: pygame.Surface,
                         head_h: int, foot_h: int) -> None:
        # Layout: top half = type counters, bottom half = recent detections.
        f_big = pygame.font.Font(None, 56)
        f_med = pygame.font.Font(None, 22)
        f_small = pygame.font.Font(None, 18)

        # Per-type count by walking the recent dict (deduped by address)
        types_by_key = _by_key()
        counts = {k: 0 for k in types_by_key}
        for d in self.detector.recent.values():
            if d.type_key in counts:
                counts[d.type_key] += 1

        # Counter columns
        col_y = head_h + 36
        cols = TRACKER_TYPES
        col_w = theme.SCREEN_W // max(1, len(cols))
        for i, t in enumerate(cols):
            cx = col_w * i + col_w // 2
            n = counts.get(t.key, 0)
            n_surf = f_big.render(str(n), True, t.color)
            surf.blit(n_surf, (cx - n_surf.get_width() // 2, col_y))
            l_surf = f_small.render(t.label, True, theme.FG_DIM)
            surf.blit(l_surf, (cx - l_surf.get_width() // 2,
                               col_y + n_surf.get_height() + 2))

        # Recent detections list
        list_x = theme.PADDING
        list_y = col_y + 110
        list_w = theme.SCREEN_W - 2 * theme.PADDING
        list_h = theme.SCREEN_H - list_y - foot_h - 12
        pygame.draw.rect(surf, (5, 5, 10), (list_x, list_y, list_w, list_h))
        pygame.draw.rect(surf, theme.DIVIDER, (list_x, list_y, list_w, list_h), 1)

        header = f_small.render(
            "RECENT DETECTIONS  (newest first)", True, theme.ACCENT)
        surf.blit(header, (list_x + 8, list_y + 6))

        recent = sorted(self.detector.recent.values(),
                        key=lambda d: d.ts, reverse=True)[:8]
        row_y = list_y + 26
        for d in recent:
            t = types_by_key.get(d.type_key)
            if not t:
                continue
            ago = max(0, int(time.time() - d.ts))
            line = (f"{t.label:24}  {d.address}  {d.rssi:>4} dBm   "
                    f"{ago:3}s ago")
            ls = f_small.render(line, True, t.color)
            surf.blit(ls, (list_x + 8, row_y))
            row_y += 18
            if row_y > list_y + list_h - 12:
                break

        # Alert overlay (red full-bleed with the tracker type pulsing)
        alerts = self.detector.alerts()
        if alerts:
            self._render_alert(surf, alerts)

    def _render_alert(self, surf: pygame.Surface,
                      alerts: list) -> None:
        # Pulsing red banner across the upper-mid of the screen
        pulse = 0.5 + 0.5 * abs((time.time() % 1.0) - 0.5) * 2  # 0..1..0
        red = (int(180 * pulse + 50), 0, 0)

        bw = theme.SCREEN_W - 40
        bh = 90
        bx = 20
        by = 90
        pygame.draw.rect(surf, red, (bx, by, bw, bh))
        pygame.draw.rect(surf, theme.ERR, (bx, by, bw, bh), 3)

        f_big = pygame.font.Font(None, 44)
        f_med = pygame.font.Font(None, 22)
        title = f_big.render("⚠  POSSIBLE TRACKER FOLLOWING", True, theme.FG)
        surf.blit(title, (bx + bw // 2 - title.get_width() // 2, by + 10))

        # Sub-line: which tracker types fired
        names = ", ".join(f"{t.label} ({mins}m)" for t, mins, _span in alerts)
        sub = f_med.render(names[:80], True, theme.FG)
        surf.blit(sub, (bx + bw // 2 - sub.get_width() // 2, by + bh - 28))
