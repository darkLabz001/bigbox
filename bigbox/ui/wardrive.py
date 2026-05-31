"""Wardriving — GPS-tagged Wi-Fi + BT sweep, WiGLE-1.4 CSV output.

Pipeline:
  1. GPSReader (bigbox/gps.py) parses NMEA from the LC86L USB dongle.
  2. Wi-Fi scan thread: `iw dev <iface> scan` every WIFI_SCAN_INTERVAL s,
     parsed to BSS records.
  3. BT scan thread: `bluetoothctl scan le on` runs, `bluetoothctl devices`
     polled every BT_SCAN_INTERVAL s.
  4. Each unique observation (BSSID/MAC + first sighting) is written to
     loot/wardrive/wardrive_<ts>.csv with the GPS fix at observation time.

Co-existence rules:
  - On entry: hardware.ensure_wifi_managed() + hardware.ensure_bluetooth_on().
    This recovers from a previous WifiAttackView that left an interface in
    monitor mode, or a FlockSeekerView that left btmon running.
  - On exit (B): kill scan subprocesses, stop BT scan, never touches mode
    of wlan0 (we never put it in monitor mode here, so nothing to undo).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pygame

from bigbox import hardware, theme, wigle, oui, kismet
from bigbox.events import Button, ButtonEvent
from bigbox.gps import GPSFix, GPSReader
from bigbox.ui.section import SectionContext


WIFI_SCAN_INTERVAL = 2.0  # Faster polling of Kismet API
BT_SCAN_INTERVAL = 5.0
LOOT_DIR = Path("loot/wardrive")


PHASE_LANDING = "landing"        # show GPS state, big "A: start" hint
PHASE_STARTING = "starting"      # hardware setup in background
PHASE_PHONE_QR = "phone_qr"      # show QR code to link phone GPS
PHASE_CAPTURING = "capturing"    # actively logging
PHASE_RESULT = "result"          # final stats after stop


@dataclass
class _Observation:
    mac: str
    type: str          # "WIFI" or "BLE"
    ssid: str = ""
    authmode: str = "[]"
    channel: int = 0
    rssi: int = -100
    vendor: str = ""   
    klass: str = ""    
    first_seen_iso: str = ""
    first_lat: float = 0.0
    first_lon: float = 0.0
    first_alt: float = 0.0
    first_acc: float = 0.0


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


from bigbox.ui.map import MapWidget

class WardriveView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.status_msg = "Ready"
        self.show_map = True 

        # GPS
        self.gps = GPSReader.get_shared()

        # Map
        self.map = MapWidget(theme.SCREEN_W - 2 * theme.PADDING, 240)

        # Capture state
        self.observed: dict[str, _Observation] = {}  # mac -> obs
        self._lock = threading.Lock()
        self.last_found: _Observation | None = None
        self.handshake_count = 0
        
        self._last_geiger = 0.0
        self._geiger_sound: Optional[pygame.mixer.Sound] = None
        self._init_geiger()
        
        # Audio worker thread
        self._audio_queue = []
        threading.Thread(target=self._audio_worker, daemon=True).start()

        self._csv_path: Path | None = None
        self._csv_handle = None
        self._capture_started: float = 0.0
        self._wifi_scan_count = 0
        self._bt_scan_count = 0

        # Kismet
        self.kismet = kismet.KismetManager()
        self._last_wifi_ts = 0
        self._last_bt_ts = 0

        # Scan threads
        self._stop = False
        self._worker_thread: threading.Thread | None = None
        self._ifaces: list[str] = []
        self._hci: str | None = None

        # Result
        self.result_msg = ""
        self.show_gps_detail = False
        
        # UI Overhaul Stats
        self.discovery_log: list[_Observation] = [] # Last 10
        self.npm_history: list[float] = [0.0] * 30   # Last 30 samples (5s each = 2.5 mins)
        self._last_sample_t = time.time()
        self._nodes_at_last_sample = 0
        self._max_npm = 10.0 # scale
        
        # Animations
        self._radar_radius = 0
        self._radar_t = time.time()

    def _draw_panel(self, surf: pygame.Surface, x: int, y: int, w: int, h: int, title: str):
        # Translucent background
        panel_bg = pygame.Surface((w, h), pygame.SRCALPHA)
        panel_bg.fill((15, 18, 28, 200))
        surf.blit(panel_bg, (x, y))
        
        # Border and Glow
        pygame.draw.rect(surf, theme.DIVIDER, (x, y, w, h), 1, border_radius=3)
        pygame.draw.line(surf, theme.ACCENT, (x, y), (x + w, y), 2)
        
        # Title
        f = pygame.font.Font(None, 20)
        ts = f.render(title.upper(), True, theme.ACCENT_DIM)
        surf.blit(ts, (x + 8, y + 4))

    def _draw_gauge(self, surf: pygame.Surface, x: int, y: int, label: str, val: float, max_val: float, color: tuple):
        w, h = 160, 45
        # Label
        f = pygame.font.Font(None, 18)
        ls = f.render(label, True, theme.FG_DIM)
        surf.blit(ls, (x, y))
        
        # Value
        v_str = f"{val:.1f}" if val < 100 else f"{int(val)}"
        if "SATS" in label: v_str = str(int(val))
        vs = f.render(v_str, True, theme.FG)
        surf.blit(vs, (x + w - vs.get_width(), y))
        
        # Bar
        bx, by, bw, bh = x, y + 20, w, 6
        pygame.draw.rect(surf, (30, 35, 45), (bx, by, bw, bh), border_radius=3)
        perc = min(1.0, val / max_val) if max_val > 0 else 0
        if perc > 0:
            pygame.draw.rect(surf, color, (bx, by, int(bw * perc), bh), border_radius=3)

    def _draw_graph(self, surf: pygame.Surface, x: int, y: int, w: int, h: int, data: list[float], title: str):
        self._draw_panel(surf, x, y, w, h, title)
        if not data: return
        
        points = []
        max_v = max(max(data), 1.0)
        for i, v in enumerate(data):
            px = x + (i * (w / (len(data) - 1)))
            py = y + h - 10 - (v / max_v) * (h - 30)
            points.append((px, py))
        
        if len(points) > 1:
            pygame.draw.lines(surf, theme.ACCENT, False, points, 2)
            # Area fill (translucent)
            fill_pts = [(points[0][0], y + h - 5)] + points + [(points[-1][0], y + h - 5)]
            fill_surf = pygame.Surface((w, h), pygame.SRCALPHA)
            rel_pts = [(p[0] - x, p[1] - y) for p in fill_pts]
            pygame.draw.polygon(fill_surf, (90, 230, 170, 40), rel_pts)
            surf.blit(fill_surf, (x, y))

    def _init_geiger(self):
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            import array
            import random
            sample_rate = 44100
            duration = 0.005 # Very short click
            n_samples = int(sample_rate * duration)
            buf = array.array('h', [0] * n_samples)
            for i in range(n_samples):
                buf[i] = random.randint(-16000, 16000) # Noise
            self._geiger_sound = pygame.mixer.Sound(buffer=buf)
            self._geiger_sound.set_volume(0.1)
        except Exception:
            pass

    def _audio_worker(self):
        """Dedicated thread for playing discovery sounds to prevent main loop stutter."""
        while not self.dismissed:
            if self._audio_queue:
                sound_type = self._audio_queue.pop(0)
                if sound_type == "beep":
                    self._do_play_beep()
                elif sound_type == "geiger":
                    if self._geiger_sound:
                        self._geiger_sound.play()
            time.sleep(0.01)

    def _play_geiger(self, npm: float):
        if not self._geiger_sound: return
        if npm < 0.1: return
        
        interval = 60.0 / max(1.0, npm)
        interval = min(2.0, interval)
        
        if time.time() - self._last_geiger > interval:
            self._last_geiger = time.time()
            self._audio_queue.append("geiger")

    def _play_beep(self) -> None:
        self._audio_queue.append("beep")

    def _do_play_beep(self) -> None:
        """Internal blip playback."""
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            import array
            sample_rate = 44100
            freq = 1200
            duration = 0.05
            n_samples = int(sample_rate * duration)
            buf = array.array('h', [0] * n_samples)
            for i in range(n_samples):
                t = i / sample_rate
                buf[i] = 8000 if (int(t * freq * 2) % 2) else -8000
            sound = pygame.mixer.Sound(buffer=buf)
            sound.set_volume(0.2)
            sound.play()
        except Exception:
            pass

    # ---------- session lifecycle ----------
    def _start_capture_async(self) -> None:
        self.phase = PHASE_STARTING
        self.status_msg = "Preparing hardware..."
        threading.Thread(target=self._start_capture_worker, daemon=True).start()

    def _start_capture_worker(self) -> None:
        # Check dependencies
        missing = hardware.check_dependencies("kismet", "bluetoothctl")
        if missing:
            self.status_msg = f"Missing: {', '.join(missing)}"
            self.phase = PHASE_LANDING
            return

        # Recover from previous tools
        hardware.ensure_wifi_managed()
        self._hci = hardware.ensure_bluetooth_on()

        # Interfaces
        all_ifaces = hardware.list_wifi_clients()
        internet_iface = hardware.get_internet_iface()
        alfa_ifaces = hardware.list_alfa_ifaces()
        
        scan_ifaces = [i for i in all_ifaces if i != internet_iface]
        if not scan_ifaces:
            scan_ifaces = all_ifaces[:1] # Use what we have

        # Try to lock interfaces
        locked_ifaces = []
        for iface in scan_ifaces:
            if hardware.request_iface(iface):
                locked_ifaces.append(iface)
        
        if not locked_ifaces:
            self.status_msg = "Error: Wi-Fi interfaces busy"
            self.phase = PHASE_LANDING
            return
        
        self._ifaces = locked_ifaces

        # Start Kismet
        self.status_msg = "Starting Kismet..."
        if not self.kismet.start(self._ifaces):
            self.status_msg = "Error: Kismet failed to start"
            for i in self._ifaces: hardware.release_iface(i)
            self.phase = PHASE_LANDING
            return

        # Setup CSV
        LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self._csv_path = LOOT_DIR / f"wardrive_{ts}.csv"
        self._csv_handle = self._csv_path.open("w", buffering=1)
        self._csv_handle.write(wigle.wigle_csv_header())
        
        self._capture_started = time.time()
        with self._lock:
            self.observed.clear()
            self.last_found = None
            self.handshake_count = 0
        self._wifi_scan_count = 0
        self._bt_scan_count = 0
        self._last_wifi_ts = 0
        self._last_bt_ts = 0
        self._stop = False
        
        # Start Polling Thread
        self._worker_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._worker_thread.start()

        self.status_msg = "Capturing..."
        self.phase = PHASE_CAPTURING

        from bigbox import background as _bg
        _bg.register(
            "wardrive",
            f"Wardrive ({len(self._ifaces)} iface)",
            "Recon",
            stop=self._stop_capture,
        )

    def _stop_capture(self) -> None:
        self._stop = True
        self.status_msg = "Stopping..."
        
        # Stop Kismet
        self.kismet.stop()
        
        # Release locks
        for iface in getattr(self, "_ifaces", []):
            hardware.release_iface(iface)
        if getattr(self, "_hci", None):
            hardware.release_bluetooth(self._hci)

        hardware.stop_bluetooth_scan()
        
        # Close CSV
        if self._csv_handle:
            try:
                self._csv_handle.flush()
                self._csv_handle.close()
            except Exception:
                pass
        self._csv_handle = None
        
        with self._lock:
            wifi_count = sum(1 for o in self.observed.values() if o.type == "WIFI")
            bt_count = sum(1 for o in self.observed.values() if o.type == "BLE")
        
        elapsed = max(0, time.time() - self._capture_started)
        self.result_msg = (
            f"{wifi_count} Wi-Fi, {bt_count} BT "
            f"in {int(elapsed)}s"
        )
        self.status_msg = self.result_msg
        from bigbox import background as _bg
        _bg.unregister("wardrive")
        self.phase = PHASE_RESULT

    def _shutdown(self) -> None:
        if self.phase == PHASE_CAPTURING:
            self._stop_capture()
        self.dismissed = True

    # ---------- record an observation ----------
    def _record(self, obs: _Observation) -> None:
        with self._lock:
            if obs.mac in self.observed:
                # Update RSSI
                if self.last_found and self.last_found.mac == obs.mac:
                    self.last_found.rssi = obs.rssi
                return
            
            fix = self.gps.latest()
            if not fix.has_fix:
                return # Need GPS for WiGLE
            
            obs.vendor, obs.klass = oui.lookup(obs.mac)
            obs.first_lat = fix.lat
            obs.first_lon = fix.lon
            obs.first_alt = fix.alt_m
            obs.first_acc = fix.accuracy_m
            obs.first_seen_iso = fix.timestamp_iso or _now_iso()
            self.observed[obs.mac] = obs
            self.last_found = obs
            
            # Update discovery log (UI)
            self.discovery_log.insert(0, obs)
            if len(self.discovery_log) > 10:
                self.discovery_log.pop()
            
            # Achievements
            from bigbox import achievements
            achievements.report_node(is_bt=(obs.type == "BLE"))

        self._play_beep()
        self.map.add_discovery_ring(obs.first_lat, obs.first_lon)

        if self._csv_handle:
            self._csv_handle.write(wigle.wigle_csv_row(
                mac=obs.mac,
                ssid=obs.ssid,
                authmode=obs.authmode,
                first_seen=obs.first_seen_iso,
                channel=obs.channel,
                rssi=obs.rssi,
                lat=obs.first_lat,
                lon=obs.first_lon,
                alt_m=obs.first_alt,
                accuracy_m=obs.first_acc,
                obs_type="WIFI" if obs.type == "WIFI" else "BT",
            ))

    def _polling_loop(self) -> None:
        """Poll Kismet API for new devices."""
        last_wifi_poll = 0.0
        last_bt_poll = 0.0
        
        while not self._stop:
            now = time.time()
            
            # WIFI Poll
            if now - last_wifi_poll >= WIFI_SCAN_INTERVAL:
                devices = self.kismet.get_wifi_devices(last_ts=self._last_wifi_ts)
                if devices:
                    max_ts = self._last_wifi_ts
                    for d in devices:
                        mac = d.get("kismet.device.base.macaddr")
                        if not mac: continue
                        
                        ts = d.get("kismet.device.base.last_time", 0)
                        if ts > max_ts: max_ts = ts
                        
                        obs = _Observation(
                            mac=mac.lower(),
                            type="WIFI",
                            ssid=d.get("kismet.device.base.commonname", ""),
                            rssi=d.get("kismet.device.base.signal", {}).get("kismet.common.signal.last_signal", -100),
                            channel=d.get("kismet.device.base.channel", 0),
                            authmode=d.get("kismet.device.base.crypt", "None")
                        )
                        self._record(obs)
                    self._last_wifi_ts = max_ts
                    with self._lock:
                        self._wifi_scan_count += 1
                last_wifi_poll = now

            # BT Poll
            if now - last_bt_poll >= BT_SCAN_INTERVAL:
                devices = self.kismet.get_bt_devices(last_ts=self._last_bt_ts)
                if devices:
                    max_ts = self._last_bt_ts
                    for d in devices:
                        mac = d.get("kismet.device.base.macaddr")
                        if not mac: continue
                        
                        ts = d.get("kismet.device.base.last_time", 0)
                        if ts > max_ts: max_ts = ts
                        
                        obs = _Observation(
                            mac=mac.lower(),
                            type="BLE",
                            ssid=d.get("kismet.device.base.commonname", ""),
                            rssi=d.get("kismet.device.base.signal", {}).get("kismet.common.signal.last_signal", -100),
                            authmode="[BLE]"
                        )
                        self._record(obs)
                    self._last_bt_ts = max_ts
                    with self._lock:
                        self._bt_scan_count += 1
                last_bt_poll = now

            time.sleep(0.5)

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._shutdown()
            return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                self._start_capture_async()
            elif ev.button is Button.Y:
                self.phase = PHASE_PHONE_QR
            return

        if self.phase == PHASE_PHONE_QR:
            if ev.button in (Button.B, Button.Y, Button.A):
                self.phase = PHASE_LANDING
            return

        if self.phase == PHASE_CAPTURING:
            if ev.button in (Button.A, Button.START):
                self._stop_capture()
            elif ev.button is Button.X:
                self.show_gps_detail = not self.show_gps_detail
            return

        if self.phase == PHASE_RESULT:
            if ev.button in (Button.A, Button.START):
                self._start_capture_async()
            elif ev.button is Button.X:
                self._trigger_upload()
            return

    def _trigger_upload(self) -> None:
        if not self._csv_path or not self._csv_path.exists():
            self.status_msg = "No file to upload"
            return
        
        creds = wigle.load_creds()
        if not creds:
            self.status_msg = "Not signed in to WiGLE"
            return
        
        self.status_msg = "Uploading..."
        
        def _worker():
            ok, msg = wigle.upload(self._csv_path, creds)
            self.status_msg = f"Upload: {msg}"
        
        threading.Thread(target=_worker, daemon=True).start()

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("RECON :: WARDRIVE (KISMET)", True, theme.ACCENT),
                  (theme.PADDING, 8))

        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20),
                         (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER,
                         (0, theme.SCREEN_H - foot_h),
                         (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        f_small = pygame.font.Font(None, 20)
        hint = self._hint()
        h_surf = f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - theme.PADDING,
                           theme.SCREEN_H - foot_h + 8))
        s_surf = f_small.render(self.status_msg[:60], True, theme.ACCENT)
        surf.blit(s_surf, (theme.PADDING, theme.SCREEN_H - foot_h + 8))

        self._render_gps_strip(surf, head_h)

        if self.phase == PHASE_LANDING:
            self._render_landing(surf, head_h)
        elif self.phase == PHASE_STARTING:
            self._render_starting(surf, head_h)
        elif self.phase == PHASE_PHONE_QR:
            self._render_phone_qr(surf, head_h)
        elif self.phase == PHASE_CAPTURING:
            self._render_capturing(surf, head_h, foot_h)
        elif self.phase == PHASE_RESULT:
            self._render_result(surf, head_h, foot_h)

        if getattr(self, "show_gps_detail", False):
            self._render_gps_detail(surf, head_h)

    def _hint(self) -> str:
        if self.phase == PHASE_LANDING:
            return "A: Start  Y: Phone GPS  B: Back"
        if self.phase == PHASE_STARTING:
            return "B: Back"
        if self.phase == PHASE_PHONE_QR:
            return "B: Back"
        if self.phase == PHASE_CAPTURING:
            return "A: Stop  X: GPS Detail  B: Back"
        if self.phase == PHASE_RESULT:
            return "A: New session  X: Upload  B: Back"
        return "B: Back"

    def _render_gps_strip(self, surf: pygame.Surface, head_h: int) -> None:
        fix = self.gps.latest()
        f = pygame.font.Font(None, 22)
        y = head_h + 8
        
        pygame.draw.rect(surf, (5, 5, 15), (0, head_h, theme.SCREEN_W, 30))
        pygame.draw.line(surf, theme.DIVIDER, (0, head_h + 30), (theme.SCREEN_W, head_h + 30))

        if not fix.device_path:
            label = "GPS: DISCONNECTED"
            color = theme.ERR
        elif not fix.has_fix:
            label = f"GPS: SEARCHING ({fix.device_path})"
            color = theme.WARN
        else:
            label = (f"GPS: {fix.device_path}  {fix.lat:.5f}, {fix.lon:.5f}  "
                     f"sats {fix.sats}  {fix.speed_kmh:.0f}km/h")
            color = theme.ACCENT

        s = f.render(label, True, color)
        surf.blit(s, (theme.PADDING, y))

    def _render_gps_detail(self, surf: pygame.Surface, head_h: int) -> None:
        fix = self.gps.latest()
        
        panel_w, panel_h = 360, 240
        px = (theme.SCREEN_W - panel_w) // 2
        py = head_h + 40
        
        pygame.draw.rect(surf, (10, 10, 20, 220), (px, py, panel_w, panel_h), border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT, (px, py, panel_w, panel_h), 2, border_radius=10)
        
        f_med = pygame.font.Font(None, 28)
        f_small = pygame.font.Font(None, 22)
        
        title = f_med.render("GPS STATUS (gpsd)", True, theme.ACCENT)
        surf.blit(title, (px + 20, py + 15))
        pygame.draw.line(surf, theme.DIVIDER, (px + 20, py + 45), (px + panel_w - 20, py + 45))
        
        lines = [
            f"Fix: {'YES' if fix.has_fix else 'NO'}",
            f"Sats: {fix.sats}",
            f"HDOP: {fix.hdop:.2f}",
            f"Accuracy: {fix.accuracy_m:.1f}m",
            f"Latitude: {fix.lat:.6f}",
            f"Longitude: {fix.lon:.6f}",
            f"Altitude: {fix.alt_m:.1f}m",
            f"Speed: {fix.speed_kmh:.1f} km/h",
            f"Path: {fix.device_path}",
            f"Time: {fix.timestamp_iso.split(' ')[1] if fix.timestamp_iso else 'N/A'}"
        ]
        
        for i, ln in enumerate(lines):
            ls = f_small.render(ln, True, theme.FG)
            surf.blit(ls, (px + 30, py + 60 + i * 20))

    def _render_landing(self, surf: pygame.Surface, head_h: int) -> None:
        f_big = pygame.font.Font(None, 48)
        f_med = pygame.font.Font(None, 24)
        
        # Cyber-glow background effect
        pygame.draw.rect(surf, (20, 30, 40), (theme.PADDING, head_h + 50, theme.SCREEN_W - 2*theme.PADDING, 300), border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT, (theme.PADDING, head_h + 50, theme.SCREEN_W - 2*theme.PADDING, 300), 2, border_radius=10)
        
        msg = f_big.render("TACTICAL WARDRIVE READY", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, head_h + 100))
        
        sub = f_med.render("Backends: Kismet (Discovery) + gpsd (Location)", True, theme.ACCENT)
        surf.blit(sub, (theme.SCREEN_W // 2 - sub.get_width() // 2, head_h + 160))
        
        hint = f_med.render("Press [A] to initiate capture sequence", True, theme.FG_DIM)
        surf.blit(hint, (theme.SCREEN_W // 2 - hint.get_width() // 2, head_h + 240))
        
    def _render_starting(self, surf: pygame.Surface, head_h: int) -> None:
        f_big = pygame.font.Font(None, 36)
        msg = f_big.render("INITIALIZING KISMET...", True, theme.ACCENT)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, head_h + 100))
        # Add a tactical scanning line
        line_y = head_h + 150 + int(math.sin(time.time() * 10) * 20)
        pygame.draw.line(surf, theme.ACCENT, (theme.PADDING, line_y), (theme.SCREEN_W - theme.PADDING, line_y), 1)

    def _render_phone_qr(self, surf: pygame.Surface, head_h: int) -> None:
        from bigbox import qr
        ip = qr.lan_ipv4()
        url = f"https://{ip}:8080/gps/link" if ip else None
        f_big = pygame.font.Font(None, 36)
        f_med = pygame.font.Font(None, 24)
        msg = f_big.render("LINK PHONE GPS", True, theme.ACCENT)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, head_h + 40))
        if not url: return
        matrix = qr.make_matrix(url)
        if matrix:
            mod_size = 6
            qr_w = (len(matrix) + 8) * mod_size
            qx, qy = (theme.SCREEN_W - qr_w) // 2, head_h + 120
            pygame.draw.rect(surf, (255, 255, 255), (qx, qy, qr_w, qr_w))
            for r, row in enumerate(matrix):
                for c, val in enumerate(row):
                    if val:
                        pygame.draw.rect(surf, (0, 0, 0), (qx + (c+4)*mod_size, qy + (r+4)*mod_size, mod_size, mod_size))

    def _render_capturing(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        with self._lock:
            wifi_count = sum(1 for o in self.observed.values() if o.type == "WIFI")
            bt_count = sum(1 for o in self.observed.values() if o.type == "BLE")
            last = self.last_found
            log = list(self.discovery_log)
        
        total_nodes = wifi_count + bt_count
        now = time.time()
        elapsed = int(now - self._capture_started)
        
        # NPM Sampling (every 5 seconds)
        if now - self._last_sample_t >= 5.0:
            nodes_diff = total_nodes - self._nodes_at_last_sample
            npm = nodes_diff * 12.0 # 60s / 5s = 12
            self.npm_history.pop(0)
            self.npm_history.append(npm)
            self._last_sample_t = now
            self._nodes_at_last_sample = total_nodes
        
        # Current NPM (for audio and HUD)
        npm_curr = self.npm_history[-1]
        self._play_geiger(npm_curr)

        # --- Layout ---
        # Top Left: MAP
        map_w, map_h = 500, 240
        mx, my = theme.PADDING, head_h + 40
        self.map.render(surf, mx, my, w=map_w, h=map_h)
        
        # Radar Pulse Animation on Map
        self._radar_radius = (self._radar_radius + 2) % 60
        pygame.draw.circle(surf, theme.ACCENT, (mx + map_w//2, my + map_h//2), self._radar_radius, 1)
        
        # Top Right: Metrics Panel
        mw, mh = 260, 240
        mxx, myy = mx + map_w + 10, my
        self._draw_panel(surf, mxx, myy, mw, mh, "Tactical Metrics")
        
        fix = self.gps.latest()
        self._draw_gauge(surf, mxx + 10, myy + 35, "SATS", fix.sats, 12.0, theme.ACCENT)
        self._draw_gauge(surf, mxx + 10, myy + 85, "HDOP", 10.0 - min(fix.hdop, 10.0), 10.0, theme.WARN)
        self._draw_gauge(surf, mxx + 10, myy + 135, "SPEED (KM/H)", fix.speed_kmh, 60.0, theme.ACCENT)
        self._draw_gauge(surf, mxx + 10, myy + 185, "ALT (M)", fix.alt_m, 1000.0, theme.FG_DIM)

        # Bottom Left: NPM Graph
        gx, gy = mx, my + map_h + 10
        gw, gh = 340, 120
        self._draw_graph(surf, gx, gy, gw, gh, self.npm_history, "Nodes Per Minute")

        # Bottom Right: Discovery Log
        lx, ly = gx + gw + 10, gy
        lw, lh = theme.SCREEN_W - lx - theme.PADDING, gh
        self._draw_panel(surf, lx, ly, lw, lh, "Discovery Log")
        
        f_log = pygame.font.Font(None, 18)
        for i, obs in enumerate(log[:5]):
            icon = "[W]" if obs.type == "WIFI" else "[B]"
            color = theme.ACCENT if obs.type == "WIFI" else theme.WARN
            name = obs.ssid or obs.mac
            if len(name) > 25: name = name[:22] + "..."
            
            line = f"{icon} {name} ({obs.rssi}dBm)"
            ls = f_log.render(line, True, color)
            surf.blit(ls, (lx + 10, ly + 30 + i * 18))

        # Big Stats Overlay
        f_huge = pygame.font.Font(None, 48)
        surf.blit(f_huge.render(str(wifi_count), True, theme.ACCENT), (mx + 10, my + 10))
        f_small = pygame.font.Font(None, 20)
        surf.blit(f_small.render("WI-FI", True, theme.FG_DIM), (mx + 10, my + 50))
        
        surf.blit(f_huge.render(str(bt_count), True, theme.WARN), (mx + 120, my + 10))
        surf.blit(f_small.render("BT", True, theme.FG_DIM), (mx + 120, my + 50))

    def _render_result(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f_big = pygame.font.Font(None, 48)
        f_med = pygame.font.Font(None, 28)
        
        pygame.draw.rect(surf, (20, 30, 40), (theme.PADDING, head_h + 50, theme.SCREEN_W - 2*theme.PADDING, 300), border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT, (theme.PADDING, head_h + 50, theme.SCREEN_W - 2*theme.PADDING, 300), 2, border_radius=10)

        title = f_big.render("MISSION ACCOMPLISHED", True, theme.ACCENT)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2, head_h + 80))

        with self._lock:
            wifi_count = sum(1 for o in self.observed.values() if o.type == "WIFI")
            bt_count = sum(1 for o in self.observed.values() if o.type == "BLE")
        
        stats = [
            f"TOTAL WI-FI APs:  {wifi_count}",
            f"TOTAL BLUETOOTH:  {bt_count}",
            f"SESSION LOOT:    {self._csv_path.name if self._csv_path else 'SAVED'}"
        ]

        for i, ln in enumerate(stats):
            ls = f_med.render(ln, True, theme.FG)
            surf.blit(ls, (theme.SCREEN_W // 2 - ls.get_width() // 2, head_h + 150 + i * 35))


