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
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pygame

from bigbox import hardware, theme, wigle
from bigbox.events import Button, ButtonEvent
from bigbox.gps import GPSFix, GPSReader
from bigbox.ui.section import SectionContext


WIFI_SCAN_INTERVAL = 5.0
BT_SCAN_INTERVAL = 8.0
LOOT_DIR = Path("loot/wardrive")


PHASE_LANDING = "landing"        # show GPS state, big "A: start" hint
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
    first_seen_iso: str = ""
    first_lat: float = 0.0
    first_lon: float = 0.0
    first_alt: float = 0.0
    first_acc: float = 0.0


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _parse_iw_scan(text: str) -> list[_Observation]:
    """Very tolerant parser for `iw dev <iface> scan` output."""
    obs: list[_Observation] = []
    cur: _Observation | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^BSS\s+([0-9a-fA-F:]{17})", line)
        if m:
            if cur:
                obs.append(cur)
            cur = _Observation(
                mac=m.group(1).lower(),
                type="WIFI",
                first_seen_iso=_now_iso(),
            )
            continue
        if not cur:
            continue
        ls = line.strip()
        if ls.startswith("freq:"):
            try:
                freq = int(ls.split(":", 1)[1].strip())
                cur.channel = _freq_to_channel(freq)
            except ValueError:
                pass
        elif ls.startswith("signal:"):
            # "signal: -50.00 dBm"
            m2 = re.search(r"-?\d+(\.\d+)?", ls)
            if m2:
                try:
                    cur.rssi = int(float(m2.group(0)))
                except ValueError:
                    pass
        elif ls.startswith("SSID:"):
            cur.ssid = ls.split(":", 1)[1].strip()
        elif ls.startswith("RSN:") or ls.startswith("WPA:"):
            # Keep first non-empty security marker; build [WPA2] etc.
            head = ls.split(":", 1)[0]
            if not cur.authmode or cur.authmode == "[]":
                cur.authmode = f"[{head}]"
            else:
                cur.authmode = cur.authmode.rstrip("]") + "][" + head + "]"
        elif "Privacy" in ls and cur.authmode == "[]":
            cur.authmode = "[WEP]"
    if cur:
        obs.append(cur)
    # Default unencrypted nets to [ESS]
    for o in obs:
        if o.authmode == "[]":
            o.authmode = "[ESS]"
        else:
            o.authmode = o.authmode + "[ESS]"
    return obs


def _freq_to_channel(freq_mhz: int) -> int:
    if 2412 <= freq_mhz <= 2484:
        if freq_mhz == 2484:
            return 14
        return (freq_mhz - 2407) // 5
    if 5170 <= freq_mhz <= 5825:
        return (freq_mhz - 5000) // 5
    if 5955 <= freq_mhz <= 7115:  # 6 GHz
        return (freq_mhz - 5950) // 5
    return 0


def _parse_bluetoothctl_devices(text: str) -> list[_Observation]:
    """`bluetoothctl devices` rows: 'Device AA:BB:CC:DD:EE:FF Name'."""
    out: list[_Observation] = []
    for line in text.splitlines():
        m = re.match(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.*)$", line.strip())
        if not m:
            continue
        out.append(_Observation(
            mac=m.group(1).lower(),
            type="BLE",
            ssid=m.group(2).strip(),
            authmode="[BLE]",
            channel=0,
            rssi=-100,
            first_seen_iso=_now_iso(),
        ))
    return out


class WardriveView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.status_msg = "Initialising..."

        # Recover from previous tools (monitor mode, btmon, etc.)
        hardware.ensure_wifi_managed()
        hardware.ensure_bluetooth_on()

        # Split interfaces: one for scanning, one for capture (if available)
        all_ifaces = hardware.list_wifi_clients()
        if not all_ifaces:
            all_ifaces = ["wlan0"]
        
        self.ifaces = all_ifaces[:1]  # Default to one for scanning
        self.cap_iface_raw: str | None = None
        self.mon_iface: str | None = None
        
        if len(all_ifaces) > 1:
            # We have at least two. Use the rest for scanning?
            # Or use one for scanning and one for capture.
            # Usually wlan1 (USB) is better for capture.
            if len(all_ifaces) >= 2:
                self.cap_iface_raw = all_ifaces[1]
                self.ifaces = [all_ifaces[0]]
            
        # GPS
        self.gps = GPSReader()
        self.gps.start()

        # Capture state
        self.observed: dict[str, _Observation] = {}  # mac -> obs
        self._lock = threading.Lock()
        self.last_found: _Observation | None = None
        self.handshake_count = 0
        
        self._csv_path: Path | None = None
        self._csv_handle = None
        self._capture_started: float = 0.0
        self._wifi_scan_count = 0
        self._bt_scan_count = 0

        # Scan threads
        self._stop = False
        self._wifi_threads: list[threading.Thread] = []
        self._bt_thread: threading.Thread | None = None
        self._bt_proc: subprocess.Popen | None = None  # the persistent `scan le on`
        self._hcxdumptool: subprocess.Popen | None = None

        # Result
        self.result_msg = ""

    # ---------- session lifecycle ----------
    def _start_capture(self) -> None:
        LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self._csv_path = LOOT_DIR / f"wardrive_{ts}.csv"
        self._csv_handle = self._csv_path.open("w", buffering=1)  # line-buffered
        self._csv_handle.write(wigle.wigle_csv_header())
        self._capture_started = time.time()
        with self._lock:
            self.observed.clear()
            self.last_found = None
            self.handshake_count = 0
        self._wifi_scan_count = 0
        self._bt_scan_count = 0
        self._stop = False
        self.phase = PHASE_CAPTURING

        # Start one thread per interface
        self._wifi_threads = []
        for iface in self.ifaces:
            t = threading.Thread(target=self._wifi_loop, args=(iface,), daemon=True)
            t.start()
            self._wifi_threads.append(t)

        self._bt_thread = threading.Thread(target=self._bt_loop, daemon=True)
        self._bt_thread.start()

        # Optional: Handshake capture if we have a spare iface
        if self.cap_iface_raw:
            self.status_msg = f"Enabling monitor on {self.cap_iface_raw}..."
            # This is blocking, but it's okay for a moment
            self.mon_iface = hardware.enable_monitor(self.cap_iface_raw)
            if self.mon_iface:
                self._start_hcxdumptool(ts)
            else:
                self.status_msg = "Monitor mode failed, scanning only."

        self.status_msg = "Capturing..."

    def _start_hcxdumptool(self, ts: str) -> None:
        if not self.mon_iface:
            return
        
        pcap_path = LOOT_DIR / f"wardrive_{ts}.pcapng"
        # hcxdumptool -i <iface> -o <out> --enable_status=1
        # Status 1 gives us periodic updates on stdout
        cmd = ["hcxdumptool", "-i", self.mon_iface, "-o", str(pcap_path), "--enable_status=1"]
        try:
            self._hcxdumptool = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            # Thread to watch for handshakes in output
            def _watcher():
                if not self._hcxdumptool or not self._hcxdumptool.stdout: return
                for line in self._hcxdumptool.stdout:
                    if self._stop: break
                    # [PMKID] or [EAPOL] typically indicates a capture
                    if "[PMKID]" in line or "[EAPOL]" in line:
                        with self._lock:
                            self.handshake_count += 1
                        self._play_beep() # Triple beep for handshake?
            
            threading.Thread(target=_watcher, daemon=True).start()
        except Exception as e:
            self.status_msg = f"hcxdumptool failed: {e}"

    def _stop_capture(self) -> None:
        self._stop = True
        # Kill hcxdumptool
        if self._hcxdumptool:
            try:
                self._hcxdumptool.terminate()
                self._hcxdumptool.wait(timeout=2)
            except Exception:
                try: self._hcxdumptool.kill()
                except Exception: pass
        self._hcxdumptool = None
        
        # Restore capture interface if needed
        if self.mon_iface:
            hardware.ensure_wifi_managed(self.cap_iface_raw)
            self.mon_iface = None

        # Bring BT down first so we don't leave bluetoothctl scanning.
        if self._bt_proc and self._bt_proc.poll() is None:
            try:
                self._bt_proc.terminate()
                self._bt_proc.wait(timeout=2)
            except Exception:
                try:
                    self._bt_proc.kill()
                except Exception:
                    pass
        self._bt_proc = None
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
            f"{wifi_count} Wi-Fi APs, {bt_count} BT devices "
            f"in {int(elapsed)}s — saved to "
            f"{self._csv_path.name if self._csv_path else '<no file>'}"
        )
        self.status_msg = self.result_msg
        self.phase = PHASE_RESULT

    def _shutdown(self) -> None:
        # called on B-exit at any phase
        if self.phase == PHASE_CAPTURING:
            self._stop_capture()
        try:
            self.gps.stop()
        except Exception:
            pass
        self.dismissed = True

    # ---------- record an observation ----------
    def _play_beep(self) -> None:
        """Short discovery blip."""
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
                # Square wave
                buf[i] = 8000 if (int(t * freq * 2) % 2) else -8000
            sound = pygame.mixer.Sound(buffer=buf)
            sound.set_volume(0.2)
            sound.play()
        except Exception:
            pass

    def _record(self, obs: _Observation) -> None:
        with self._lock:
            if obs.mac in self.observed:
                # Update RSSI for last_found display even if already known
                if self.last_found and self.last_found.mac == obs.mac:
                    self.last_found.rssi = obs.rssi
                return
            
            fix = self.gps.latest()
            if not fix.has_fix:
                # WiGLE will reject rows with no GPS — skip.
                return
            
            obs.first_lat = fix.lat
            obs.first_lon = fix.lon
            obs.first_alt = fix.alt_m
            obs.first_acc = fix.accuracy_m
            obs.first_seen_iso = fix.timestamp_iso or _now_iso()
            self.observed[obs.mac] = obs
            self.last_found = obs

        self._play_beep()

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

    # ---------- scan loops ----------
    def _wifi_loop(self, iface: str) -> None:
        while not self._stop:
            try:
                proc = subprocess.run(
                    ["iw", "dev", iface, "scan"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    text=True, timeout=15,
                )
                if proc.returncode == 0:
                    for o in _parse_iw_scan(proc.stdout):
                        self._record(o)
                    with self._lock:
                        self._wifi_scan_count += 1
            except Exception:
                pass
            # Sleep but stay responsive to stop
            t = time.time()
            while time.time() - t < WIFI_SCAN_INTERVAL and not self._stop:
                time.sleep(0.1)

    def _bt_loop(self) -> None:
        # Start a persistent `scan le on` so the controller keeps probing.
        try:
            self._bt_proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            assert self._bt_proc.stdin is not None
            self._bt_proc.stdin.write("scan le on\n")
            self._bt_proc.stdin.flush()
        except Exception:
            self._bt_proc = None

        while not self._stop:
            try:
                proc = subprocess.run(
                    ["bluetoothctl", "devices"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    text=True, timeout=5,
                )
                if proc.returncode == 0:
                    for o in _parse_bluetoothctl_devices(proc.stdout):
                        self._record(o)
                    with self._lock:
                        self._bt_scan_count += 1
            except Exception:
                pass
            t = time.time()
            while time.time() - t < BT_SCAN_INTERVAL and not self._stop:
                time.sleep(0.1)

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._shutdown()
            return

        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                self._start_capture()
            return

        if self.phase == PHASE_CAPTURING:
            if ev.button in (Button.A, Button.START):
                self._stop_capture()
            return

        if self.phase == PHASE_RESULT:
            if ev.button in (Button.A, Button.START):
                # Start another session
                self._start_capture()
            elif ev.button is Button.X:
                self._trigger_upload()
            return

    def _trigger_upload(self) -> None:
        if not self._csv_path or not self._csv_path.exists():
            self.status_msg = "No file to upload"
            return
        
        creds = wigle.load_creds()
        if not creds:
            self.status_msg = "Not signed in to WiGLE (use Web UI)"
            return
        
        self.status_msg = "Uploading to WiGLE..."
        
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
        surf.blit(f_title.render("RECON :: WARDRIVE", True, theme.ACCENT),
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

        # GPS strip — always visible
        self._render_gps_strip(surf, head_h)

        if self.phase == PHASE_LANDING:
            self._render_landing(surf, head_h)
        elif self.phase == PHASE_CAPTURING:
            self._render_capturing(surf, head_h, foot_h)
        elif self.phase == PHASE_RESULT:
            self._render_result(surf, head_h, foot_h)

    def _hint(self) -> str:
        if self.phase == PHASE_LANDING:
            return "A: Start  B: Back"
        if self.phase == PHASE_CAPTURING:
            return "A: Stop  B: Back"
        if self.phase == PHASE_RESULT:
            return "A: New session  X: Upload  B: Back"
        return "B: Back"

    def _render_gps_strip(self, surf: pygame.Surface, head_h: int) -> None:
        fix = self.gps.latest()
        f = pygame.font.Font(None, 22)
        y = head_h + 8
        if not fix.device_path:
            label = "GPS: NO DEVICE (plug in LC86L)"
            color = theme.ERR
        elif not fix.has_fix:
            label = f"GPS: SEARCHING ({fix.device_path})"
            color = theme.WARN
        else:
            label = (f"GPS: FIX  {fix.lat:.5f}, {fix.lon:.5f}  "
                     f"alt {fix.alt_m:.0f}m  hdop {fix.hdop:.1f}  "
                     f"sats {fix.sats}  {fix.speed_kmh:.0f}km/h")
            color = theme.ACCENT
        s = f.render(label, True, color)
        surf.blit(s, (theme.PADDING, y))

    def _render_landing(self, surf: pygame.Surface, head_h: int) -> None:
        f_big = pygame.font.Font(None, 44)
        f_med = pygame.font.Font(None, 24)
        msg = f_big.render("Ready to wardrive", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                        head_h + 80))
        sub = f_med.render(
            "Press A to begin capturing Wi-Fi + Bluetooth with GPS.",
            True, theme.FG_DIM)
        surf.blit(sub, (theme.SCREEN_W // 2 - sub.get_width() // 2,
                        head_h + 140))
        
        ifaces_str = ", ".join(self.ifaces)
        sub2 = f_med.render(
            f"Scan ifaces: {ifaces_str}   Output: loot/wardrive/",
            True, theme.FG_DIM)
        surf.blit(sub2, (theme.SCREEN_W // 2 - sub2.get_width() // 2,
                         head_h + 180))
        sub3 = f_med.render(
            "Sign in to WiGLE via the web UI to upload sessions.",
            True, theme.FG_DIM)
        surf.blit(sub3, (theme.SCREEN_W // 2 - sub3.get_width() // 2,
                         head_h + 220))

    def _render_capturing(self, surf: pygame.Surface,
                          head_h: int, foot_h: int) -> None:
        with self._lock:
            wifi_count = sum(1 for o in self.observed.values() if o.type == "WIFI")
            bt_count = sum(1 for o in self.observed.values() if o.type == "BLE")
            hand_count = self.handshake_count
            last = self.last_found

        elapsed = int(time.time() - self._capture_started)

        f_huge = pygame.font.Font(None, 80)
        f_med = pygame.font.Font(None, 26)
        f_small = pygame.font.Font(None, 20)

        # Big counter row
        cy = head_h + 80
        col_w = theme.SCREEN_W // 3

        wifi_n = f_huge.render(str(wifi_count), True, theme.ACCENT)
        surf.blit(wifi_n, (col_w // 2 - wifi_n.get_width() // 2, cy))
        wifi_l = f_med.render("WI-FI", True, theme.FG_DIM)
        surf.blit(wifi_l, (col_w // 2 - wifi_l.get_width() // 2,
                           cy + wifi_n.get_height() + 4))

        bt_n = f_huge.render(str(bt_count), True, theme.ACCENT)
        surf.blit(bt_n, (col_w + col_w // 2 - bt_n.get_width() // 2, cy))
        bt_l = f_med.render("BLUETOOTH", True, theme.FG_DIM)
        surf.blit(bt_l, (col_w + col_w // 2 - bt_l.get_width() // 2,
                         cy + bt_n.get_height() + 4))
        
        # Handshake counter (only show if we have a capture iface)
        hand_n = f_huge.render(str(hand_count), True, theme.ACCENT if hand_count > 0 else theme.FG_DIM)
        surf.blit(hand_n, (2 * col_w + col_w // 2 - hand_n.get_width() // 2, cy))
        hand_l = f_med.render("HANDSHAKES", True, theme.FG_DIM)
        surf.blit(hand_l, (2 * col_w + col_w // 2 - hand_l.get_width() // 2,
                           cy + hand_n.get_height() + 4))

        # Last found box
        if last:
            ly = cy + 130
            pygame.draw.rect(surf, theme.BG_ALT, (theme.PADDING, ly, theme.SCREEN_W - 2*theme.PADDING, 60), border_radius=5)
            pygame.draw.rect(surf, theme.ACCENT, (theme.PADDING, ly, theme.SCREEN_W - 2*theme.PADDING, 60), 1, border_radius=5)
            
            l_title = f_small.render("LAST DISCOVERY", True, theme.ACCENT)
            surf.blit(l_title, (theme.PADDING + 10, ly + 8))
            
            ssid = last.ssid or "<hidden>"
            if len(ssid) > 30: ssid = ssid[:27] + "..."
            info = f"{ssid} [{last.mac}]  {last.rssi}dBm  {last.authmode}"
            l_info = f_med.render(info, True, theme.FG)
            surf.blit(l_info, (theme.PADDING + 10, ly + 30))

        # Bottom strip: elapsed, file, scan counts
        sy = theme.SCREEN_H - foot_h - 60
        info = (f"elapsed: {elapsed}s   wifi scans: {self._wifi_scan_count}  "
                f"bt polls: {self._bt_scan_count}")
        if self.mon_iface:
            info += f"   cap: {self.mon_iface}"
        info_s = f_small.render(info, True, theme.FG_DIM)
        surf.blit(info_s, (theme.PADDING, sy))
        if self._csv_path:
            ps = f_small.render(f"-> {self._csv_path}", True, theme.FG_DIM)
            surf.blit(ps, (theme.PADDING, sy + 22))

    def _render_result(self, surf: pygame.Surface,
                       head_h: int, foot_h: int) -> None:
        f_big = pygame.font.Font(None, 36)
        f_med = pygame.font.Font(None, 22)

        title = f_big.render("SESSION SAVED", True, theme.ACCENT)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2,
                          head_h + 50))

        with self._lock:
            wifi_count = sum(1 for o in self.observed.values() if o.type == "WIFI")
            bt_count = sum(1 for o in self.observed.values() if o.type == "BLE")
            hand_count = self.handshake_count
        
        lines = [
            f"Wi-Fi APs:  {wifi_count}",
            f"Bluetooth:  {bt_count}",
            f"Handshakes: {hand_count}",
        ]
        if self._csv_path:
            lines.append(f"CSV File:   {self._csv_path.name}")
        lines.append("Press X to upload to WiGLE now.")

        for i, ln in enumerate(lines):
            ls = f_med.render(ln, True, theme.FG)
            surf.blit(ls, (theme.SCREEN_W // 2 - ls.get_width() // 2,
                           head_h + 110 + i * 30))

