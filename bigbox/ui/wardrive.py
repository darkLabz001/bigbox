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

    def _init_geiger(self):

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
        f_big = pygame.font.Font(None, 44)
        f_med = pygame.font.Font(None, 24)
        msg = f_big.render("Wardriver Ready", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                        head_h + 80))
        sub = f_med.render("Using Kismet for discovery and gpsd for location.",
                        True, theme.FG_DIM)
        surf.blit(sub, (theme.SCREEN_W // 2 - sub.get_width() // 2,
                        head_h + 140))
        
    def _render_starting(self, surf: pygame.Surface, head_h: int) -> None:
        f_big = pygame.font.Font(None, 36)
        msg = f_big.render("Initialising Kismet...", True, theme.ACCENT)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, head_h + 100))
        # Optional: draw a spinner or progress bar

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

        elapsed = int(time.time() - self._capture_started)
        total_nodes = wifi_count + bt_count
        npm = (total_nodes / (elapsed / 60.0)) if elapsed > 10 else 0
        self._play_geiger(npm)

        if self.show_map:
            self.map.render(surf, theme.PADDING, head_h + 40)
            hx, hy = theme.PADDING + 10, head_h + 50
            f_small = pygame.font.Font(None, 20)
            surf.blit(f_small.render(f"WIFI: {wifi_count}", True, theme.ACCENT), (hx, hy))
            surf.blit(f_small.render(f"BT:   {bt_count}", True, theme.ACCENT), (hx, hy + 20))
        
        # Last found box
        ly = head_h + 290
        pygame.draw.rect(surf, theme.BG_ALT, (theme.PADDING, ly, theme.SCREEN_W - 2*theme.PADDING, 60), border_radius=5)
        if last:
            f_med = pygame.font.Font(None, 24)
            info = f"{last.ssid or last.mac} ({last.rssi}dBm)"
            surf.blit(f_med.render(info, True, theme.FG), (theme.PADDING + 10, ly + 20))

    def _render_result(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f_big = pygame.font.Font(None, 36)
        msg = f_big.render("SESSION SAVED", True, theme.ACCENT)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2, head_h + 80))
        f_med = pygame.font.Font(None, 24)
        surf.blit(f_med.render(self.result_msg, True, theme.FG), (theme.SCREEN_W // 2 - 100, head_h + 140))


