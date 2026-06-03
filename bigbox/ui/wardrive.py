"""Wardriving PRO 3.5 — ported from neobox, merged with bigbox infra.

Engine (from neobox's "Wardriving PRO 3.5"):
  - hcxdumptool 6.x on a monitor-capable adapter -> pcapng (PMKID/EAPOL
    handshakes captured while driving). hcxpcapngtool polls the pcap for
    AP / hash counts.
  - Bluetooth scan via hcitool/bluetoothctl, logged to WiGLE.
  - Auto-picks the monitor-capable iface by querying `iw phy` (no hardcoded
    wlanN), and restores it to managed mode on exit.

Merged-in bigbox strengths (so nothing is lost vs the old Kismet view):
  - Live map (MapWidget) with discovery rings.
  - Per-AP Wi-Fi -> WiGLE CSV logging: a parallel tshark reader pulls
    beacons/probe-responses off the same monitor iface and writes WiGLE
    rows with the current GPS fix (best-effort; degrades to pcap-only).
  - bigbox GPS abstraction (GPSReader: serial/gpsd/phone), bigbox.qr for the
    phone-GPS link, bigbox.wigle for CSV header/rows + upload, loot dir.

Safety: monitor mode disconnects the chosen adapter (internet/SSH/web drop
until exit), so capture is opt-in behind the LANDING screen's "A: Start",
and the iface is always restored to managed mode on stop.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pygame

from bigbox import oui, qr, theme, wigle
from bigbox.events import Button, ButtonEvent
from bigbox.gps import GPSReader
from bigbox.ui.map import MapWidget
from bigbox.ui.section import SectionContext

LOOT_DIR = Path("loot/wardrive")

PHASE_LANDING = "landing"
PHASE_CAPTURING = "capturing"
PHASE_RESULT = "result"


@dataclass
class _Obs:
    mac: str
    type: str          # WIFI | BT
    ssid: str = ""
    authmode: str = "[]"
    channel: int = 0
    rssi: int = -100


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class WardriveView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_LANDING
        self.status_msg = "Ready"

        self.gps = GPSReader.get_shared()
        self.map = MapWidget(theme.SCREEN_W - 2 * theme.PADDING, 150)

        # counters
        self.aps_found = 0
        self.bt_found = 0
        self.verified_hashes = 0
        self._seen_bt: set[str] = set()
        self._seen_wifi: set[str] = set()
        self._lock = threading.Lock()

        # UI feeds
        self.log_buffer: deque[str] = deque(maxlen=10)
        self.signal_history: deque[int] = deque([0] * 60, maxlen=60)
        self._last_graph_update = 0.0

        # adapters (resolved at start so a hot-plugged Alfa is picked up)
        self.wifi_iface: Optional[str] = None
        self.bt_iface = "hci1"
        self._iface_was_default_route = False

        # paths (created on start)
        self._csv_path: Optional[Path] = None
        self._pcap_path: Optional[Path] = None
        self._capture_started = 0.0

        self._stop_event = threading.Event()
        self._wifi_proc: Optional[subprocess.Popen] = None
        self._tshark_proc: Optional[subprocess.Popen] = None
        self.result_msg = ""
        self.show_gps_detail = False

        self._qr_matrix = self._make_qr_matrix()

    # ---------- adapter discovery ----------
    def _pick_monitor_iface(self) -> Optional[str]:
        """Return a wlan iface whose phy supports monitor mode, else None.
        Parsing `iw phy <phy> info` is the only reliable test."""
        try:
            out = subprocess.check_output(["/usr/sbin/iw", "dev"], text=True,
                                          stderr=subprocess.DEVNULL)
        except Exception:
            try:
                out = subprocess.check_output(["iw", "dev"], text=True,
                                              stderr=subprocess.DEVNULL)
            except Exception:
                return None
        cur_phy = None
        ifaces: list[tuple[str, str]] = []
        for line in out.splitlines():
            s = line.strip()
            if line.startswith("phy#"):
                cur_phy = s[4:]
            elif s.startswith("Interface "):
                ifaces.append((cur_phy, s.split()[1]))
        for phy, name in ifaces:
            try:
                pinfo = subprocess.check_output(
                    ["iw", "phy", f"phy{phy}", "info"], text=True,
                    stderr=subprocess.DEVNULL)
            except Exception:
                continue
            if "Supported interface modes" in pinfo:
                tail = pinfo.split("Supported interface modes", 1)[1].split("\n\n", 1)[0]
                if "* monitor" in tail:
                    return name
        return None

    def _iface_is_default_route(self, iface: str) -> bool:
        try:
            out = subprocess.check_output(["ip", "route", "show", "default"],
                                          text=True, stderr=subprocess.DEVNULL)
            return f" dev {iface} " in (" " + out + " ")
        except Exception:
            return False

    # ---------- phone-GPS QR (bigbox web link) ----------
    def _make_qr_matrix(self):
        try:
            ip = qr.lan_ipv4()
            if not ip:
                return None
            return qr.make_matrix(f"https://{ip}:8080/gps/link")
        except Exception:
            return None

    # ---------- session lifecycle ----------
    def _start(self) -> None:
        self.wifi_iface = self._pick_monitor_iface()
        LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        self._csv_path = LOOT_DIR / f"wardrive-{ts}.csv"
        self._pcap_path = LOOT_DIR / f"capture-{ts}.pcapng"
        try:
            with self._csv_path.open("w", newline="") as f:
                f.write(wigle.wigle_csv_header())
        except Exception:
            pass

        self.aps_found = self.bt_found = self.verified_hashes = 0
        self._seen_bt.clear()
        self._seen_wifi.clear()
        self._capture_started = time.time()
        self._stop_event.clear()
        self.phase = PHASE_CAPTURING
        self.status_msg = "Capturing..."

        threading.Thread(target=self._wifi_engine, daemon=True).start()
        threading.Thread(target=self._wifi_wigle_loop, daemon=True).start()
        threading.Thread(target=self._verification_loop, daemon=True).start()
        threading.Thread(target=self._bt_engine, daemon=True).start()

        try:
            from bigbox import background as _bg
            _bg.register("wardrive", "Wardrive PRO 3.5", "Recon", stop=self._stop_capture)
        except Exception:
            pass

    def _wifi_engine(self) -> None:
        if not self.wifi_iface:
            self.log_buffer.append("[!] no monitor-capable iface")
            self.log_buffer.append("[!] plug in Alfa AWUS036ACS")
            self.log_buffer.append("[!] WiFi capture disabled")
            return
        self._iface_was_default_route = self._iface_is_default_route(self.wifi_iface)
        if self._iface_was_default_route:
            self.log_buffer.append(f"[!] {self.wifi_iface} is your internet")
            self.log_buffer.append("[!] WiFi/SSH drop until exit")
        self.log_buffer.append(f"[*] using {self.wifi_iface} (monitor)")
        cmd = ["sudo", "hcxdumptool", "-i", self.wifi_iface,
               "-w", str(self._pcap_path), "-F", "--rds=1"]
        try:
            self._wifi_proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.log_buffer.append(f"[!] hcxdumptool failed: {exc}")

    def _wifi_wigle_loop(self) -> None:
        """Per-AP Wi-Fi -> WiGLE rows: read beacons/probe-resps off the monitor
        iface with tshark, in parallel with hcxdumptool. Best-effort: if tshark
        is missing or the iface never goes to monitor, Wi-Fi still lands in the
        pcap for handshakes — we just don't get live WiGLE rows."""
        if not self.wifi_iface:
            return
        # Give hcxdumptool a moment to flip the iface into monitor mode.
        if self._stop_event.wait(4.0):
            return
        fields = ["-e", "wlan.bssid", "-e", "wlan.ssid",
                  "-e", "wlan_radio.channel", "-e", "wlan_radio.signal_dbm"]
        cmd = ["sudo", "tshark", "-i", self.wifi_iface, "-l", "-n",
               "-Y", "wlan.fc.type_subtype==0x0008 || wlan.fc.type_subtype==0x0005",
               "-T", "fields", "-E", "separator=|", *fields]
        try:
            self._tshark_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except FileNotFoundError:
            self.log_buffer.append("[!] tshark missing: WiFi->WiGLE off")
            return
        except Exception:
            return
        for line in self._tshark_proc.stdout:
            if self._stop_event.is_set():
                break
            parts = line.rstrip("\n").split("|")
            if not parts or not parts[0]:
                continue
            bssid = parts[0].lower()
            ssid = parts[1] if len(parts) > 1 else ""
            try:
                chan = int(parts[2]) if len(parts) > 2 and parts[2] else 0
            except ValueError:
                chan = 0
            try:
                rssi = int(float(parts[3])) if len(parts) > 3 and parts[3] else -100
            except ValueError:
                rssi = -100
            self._record_wifi(bssid, ssid, chan, rssi)

    def _record_wifi(self, mac: str, ssid: str, chan: int, rssi: int) -> None:
        with self._lock:
            if mac in self._seen_wifi:
                return
            fix = self.gps.latest()
            if not fix.has_fix:
                return  # WiGLE needs a location
            self._seen_wifi.add(mac)
            self.aps_found = max(self.aps_found, len(self._seen_wifi))
            vendor, _ = oui.lookup(mac)
            self.log_buffer.append(f"[W] {(ssid or mac)[:14]} | {vendor[:8]}")
        self._write_wigle(mac, ssid, "[WIFI]", chan, rssi, "WIFI", fix)
        try:
            self.map.add_discovery_ring(fix.lat, fix.lon)
        except Exception:
            pass

    def _verification_loop(self) -> None:
        ap_re = re.compile(r"ESSID \(total unique\)\.+:\s*(\d+)")
        eapol_re = re.compile(r"EAPOL M1 messages \(total\)\.+:\s*(\d+)")
        pmkid_re = re.compile(r"PMKID \(total\)\.+:\s*(\d+)")
        while not self._stop_event.is_set():
            p = self._pcap_path
            if p and p.exists() and p.stat().st_size > 256:
                try:
                    out = subprocess.check_output(
                        ["hcxpcapngtool", str(p)], text=True,
                        stderr=subprocess.STDOUT, timeout=8)
                    ap = ap_re.search(out)
                    eapol = eapol_re.search(out)
                    pmkid = pmkid_re.search(out)
                    new_aps = int(ap.group(1)) if ap else 0
                    new_hashes = ((int(eapol.group(1)) if eapol else 0) +
                                  (int(pmkid.group(1)) if pmkid else 0))
                    with self._lock:
                        if new_aps > self.aps_found:
                            self.aps_found = new_aps
                        if new_hashes > self.verified_hashes:
                            self.log_buffer.append(f"[*] HASH CAPTURED: {new_hashes}")
                            self.verified_hashes = new_hashes
                except Exception:
                    pass
            self._stop_event.wait(5)

    def _bt_engine(self) -> None:
        if not os.path.exists(f"/sys/class/bluetooth/{self.bt_iface}"):
            if os.path.exists("/sys/class/bluetooth/hci0"):
                self.bt_iface = "hci0"
                self.log_buffer.append("[*] BT: using onboard hci0")
            else:
                self.log_buffer.append("[!] no BT controller")
                return
        subprocess.run(["sudo", "bluetoothctl", "power", "on"], capture_output=True)
        while not self._stop_event.is_set():
            try:
                out = subprocess.check_output(
                    ["sudo", "hcitool", "-i", self.bt_iface, "scan", "--flush"],
                    text=True, stderr=subprocess.DEVNULL).splitlines()
                for line in out:
                    m = re.search(r"([0-9A-F:]{17})\s+(.*)", line)
                    if not m:
                        continue
                    mac, name = m.group(1).lower(), m.group(2)
                    with self._lock:
                        if mac in self._seen_bt:
                            continue
                        self._seen_bt.add(mac)
                        self.bt_found += 1
                        vendor, _ = oui.lookup(mac)
                        self.log_buffer.append(f"[B] {name[:12]} | {vendor[:8]}")
                    fix = self.gps.latest()
                    if fix.has_fix:
                        self._write_wigle(mac, name, "[BT]", 0, -70, "BT", fix)
                subprocess.run(["sudo", "timeout", "2s", "hcitool", "-i",
                                self.bt_iface, "lescan"], capture_output=True)
            except Exception:
                pass
            self._stop_event.wait(2)

    def _write_wigle(self, mac, ssid, auth, chan, rssi, obs_type, fix) -> None:
        if not self._csv_path:
            return
        try:
            with self._csv_path.open("a", newline="") as f:
                f.write(wigle.wigle_csv_row(
                    mac=mac, ssid=ssid, authmode=auth,
                    first_seen=fix.timestamp_iso or _now_iso(),
                    channel=chan, rssi=rssi, lat=fix.lat, lon=fix.lon,
                    alt_m=fix.alt_m, accuracy_m=fix.accuracy_m, obs_type=obs_type))
        except Exception:
            pass

    def _stop_capture(self) -> None:
        self._stop_event.set()
        self.status_msg = "Stopping..."
        for proc in (self._wifi_proc, self._tshark_proc):
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
        subprocess.run(["sudo", "pkill", "-f", "hcxdumptool"], capture_output=True)
        subprocess.run(["sudo", "pkill", "-f", "tshark"], capture_output=True)
        # Restore the monitor iface to managed mode + reconnect if it was our
        # internet path.
        if self.wifi_iface:
            subprocess.run(["sudo", "ip", "link", "set", self.wifi_iface, "down"],
                           capture_output=True)
            subprocess.run(["sudo", "iw", "dev", self.wifi_iface, "set", "type",
                            "managed"], capture_output=True)
            subprocess.run(["sudo", "ip", "link", "set", self.wifi_iface, "up"],
                           capture_output=True)
            if self._iface_was_default_route:
                subprocess.run(["sudo", "nmcli", "device", "connect",
                                self.wifi_iface], capture_output=True)
        try:
            from bigbox import background as _bg
            _bg.unregister("wardrive")
        except Exception:
            pass
        elapsed = int(max(0, time.time() - self._capture_started))
        self.result_msg = (f"{self.aps_found} Wi-Fi, {self.bt_found} BT, "
                           f"{self.verified_hashes} hashes in {elapsed}s")
        self.status_msg = self.result_msg
        self.phase = PHASE_RESULT

    def _stop(self) -> None:  # crash-recovery hook (_STOP_METHODS)
        if self.phase == PHASE_CAPTURING:
            self._stop_capture()

    def _shutdown(self) -> None:
        if self.phase == PHASE_CAPTURING:
            self._stop_capture()
        self.dismissed = True

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return
        if ev.button is Button.B:
            self._shutdown()
            return
        if self.phase == PHASE_LANDING:
            if ev.button is Button.A:
                self._start()
        elif self.phase == PHASE_CAPTURING:
            if ev.button in (Button.A, Button.START):
                self._stop_capture()
            elif ev.button is Button.X:
                self.show_gps_detail = not self.show_gps_detail
        elif self.phase == PHASE_RESULT:
            if ev.button in (Button.A, Button.START):
                self._start()
            elif ev.button is Button.X:
                self._trigger_upload()

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
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1), (theme.SCREEN_W, head_h - 1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("RECON :: WARDRIVING PRO 3.5", True, theme.ACCENT),
                  (theme.PADDING, 8))

        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20), (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER, (0, theme.SCREEN_H - foot_h),
                         (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        f_small = pygame.font.Font(None, 20)
        hint = self._hint()
        h_surf = f_small.render(hint, True, theme.FG_DIM)
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - theme.PADDING,
                           theme.SCREEN_H - foot_h + 8))
        surf.blit(f_small.render(self.status_msg[:60], True, theme.ACCENT),
                  (theme.PADDING, theme.SCREEN_H - foot_h + 8))

        if self.phase == PHASE_LANDING:
            self._render_landing(surf, head_h)
        elif self.phase == PHASE_CAPTURING:
            self._render_capturing(surf, head_h)
        elif self.phase == PHASE_RESULT:
            self._render_result(surf, head_h)

        if self.show_gps_detail:
            self._render_gps_detail(surf, head_h)

    def _hint(self) -> str:
        return {
            PHASE_LANDING: "A: Start capture  B: Back",
            PHASE_CAPTURING: "A: Stop  X: GPS detail  B: Back",
            PHASE_RESULT: "A: New run  X: Upload WiGLE  B: Back",
        }.get(self.phase, "B: Back")

    def _gps_tile(self, surf, x, y, w, h) -> None:
        fix = self.gps.latest()
        pygame.draw.rect(surf, theme.BG_ALT, (x, y, w, h), border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT if fix.has_fix else theme.DIVIDER,
                         (x, y, w, h), 1, border_radius=8)
        f = pygame.font.Font(None, 20)
        fs = pygame.font.Font(None, 18)
        if not fix.has_fix:
            if self._qr_matrix:
                mod = 3
                qx = x + (w - len(self._qr_matrix) * mod) // 2
                pygame.draw.rect(surf, (255, 255, 255),
                                 (qx - 4, y + 6, len(self._qr_matrix) * mod + 8,
                                  len(self._qr_matrix) * mod + 8))
                for r, row in enumerate(self._qr_matrix):
                    for c, v in enumerate(row):
                        if v:
                            pygame.draw.rect(surf, (0, 0, 0),
                                             (qx + c * mod, y + 10 + r * mod, mod, mod))
            surf.blit(fs.render("SCAN: PHONE GPS LINK", True, theme.ACCENT),
                      (x + 8, y + h - 18))
        else:
            surf.blit(fs.render(f"GPS LINK: {fix.device_path}", True, theme.WARN), (x + 8, y + 8))
            surf.blit(f.render(f"LAT {fix.lat:.5f}", True, theme.FG), (x + 8, y + 32))
            surf.blit(f.render(f"LON {fix.lon:.5f}", True, theme.FG), (x + 8, y + 54))
            surf.blit(fs.render(f"sats {fix.sats}  acc {fix.accuracy_m:.0f}m", True, theme.FG_DIM),
                      (x + 8, y + 80))

    def _render_landing(self, surf, head_h) -> None:
        f_big = pygame.font.Font(None, 44)
        f_med = pygame.font.Font(None, 24)
        self._gps_tile(surf, theme.PADDING, head_h + 20, 200, 110)
        x = theme.PADDING + 220
        surf.blit(f_big.render("WARDRIVE READY", True, theme.FG), (x, head_h + 30))
        lines = [
            "Engine: hcxdumptool (handshakes) + BT + tshark/WiGLE",
            "GPS: scan the QR with your phone, or use a GPS dongle",
            "Monitor mode will drop internet on the Alfa until you exit.",
            "",
            "Press [A] to start capture.",
        ]
        for i, ln in enumerate(lines):
            surf.blit(f_med.render(ln, True, theme.ACCENT if i == 4 else theme.FG_DIM),
                      (x, head_h + 80 + i * 26))

    def _render_capturing(self, surf, head_h) -> None:
        now = time.time()
        with self._lock:
            wifi, bt, hashes = self.aps_found, self.bt_found, self.verified_hashes
            log = list(self.log_buffer)
        if now - self._last_graph_update >= 1.0:
            self.signal_history.append(wifi + bt)
            self._last_graph_update = now

        # GPS tile (top-left) + stats/graph (top-right)
        self._gps_tile(surf, theme.PADDING, head_h + 12, 200, 116)
        sx, sy = theme.PADDING + 210, head_h + 12
        sw, sh = theme.SCREEN_W - sx - theme.PADDING, 116
        pygame.draw.rect(surf, theme.BG_ALT, (sx, sy, sw, sh), border_radius=8)
        pygame.draw.rect(surf, theme.ACCENT, (sx, sy, sw, sh), 1, border_radius=8)
        f_huge = pygame.font.Font(None, 46)
        f_small = pygame.font.Font(None, 18)
        cols = [("WIFI", wifi, theme.ACCENT), ("BT", bt, theme.WARN),
                ("HASHES", hashes, theme.ERR if hashes else theme.FG_DIM)]
        for i, (lab, val, col) in enumerate(cols):
            cx = sx + 12 + i * 90
            surf.blit(f_small.render(lab, True, theme.FG_DIM), (cx, sy + 6))
            surf.blit(f_huge.render(str(val), True, col), (cx, sy + 20))
        # signal-density graph
        gx, gy, gw, gh = sx + 12, sy + 70, sw - 24, 38
        pygame.draw.rect(surf, (0, 0, 0), (gx, gy, gw, gh))
        if len(self.signal_history) > 1:
            mx = max(max(self.signal_history), 5)
            pts = [(gx + i * (gw / 59), gy + gh - (v / mx * gh))
                   for i, v in enumerate(self.signal_history)]
            pygame.draw.lines(surf, theme.ACCENT, False, pts, 1)

        # map (middle)
        my = head_h + 138
        self.map.render(surf, theme.PADDING, my)

        # live log (bottom)
        ly = my + 158
        lh = theme.SCREEN_H - ly - 38
        pygame.draw.rect(surf, (0, 0, 0), (theme.PADDING, ly, theme.SCREEN_W - 2 * theme.PADDING, lh),
                         border_radius=6)
        pygame.draw.rect(surf, theme.ACCENT, (theme.PADDING, ly, theme.SCREEN_W - 2 * theme.PADDING, lh),
                         1, border_radius=6)
        f_log = pygame.font.Font(None, 18)
        yy = ly + 6
        for entry in log:
            col = theme.FG
            if "[W]" in entry:
                col = theme.ACCENT
            elif "[B]" in entry:
                col = theme.WARN
            elif "[*]" in entry:
                col = theme.ERR
            elif "[!]" in entry:
                col = theme.WARN
            surf.blit(f_log.render(entry, True, col), (theme.PADDING + 8, yy))
            yy += 15
            if yy > ly + lh - 14:
                break

    def _render_result(self, surf, head_h) -> None:
        f_big = pygame.font.Font(None, 44)
        f_med = pygame.font.Font(None, 26)
        pygame.draw.rect(surf, (20, 30, 40),
                         (theme.PADDING, head_h + 50, theme.SCREEN_W - 2 * theme.PADDING, 280),
                         border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT,
                         (theme.PADDING, head_h + 50, theme.SCREEN_W - 2 * theme.PADDING, 280),
                         2, border_radius=10)
        title = f_big.render("CAPTURE COMPLETE", True, theme.ACCENT)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2, head_h + 80))
        stats = [
            f"WI-FI APs:   {self.aps_found}",
            f"BLUETOOTH:   {self.bt_found}",
            f"HANDSHAKES:  {self.verified_hashes}",
            f"LOOT:        {self._csv_path.name if self._csv_path else 'saved'}",
        ]
        for i, ln in enumerate(stats):
            ls = f_med.render(ln, True, theme.FG)
            surf.blit(ls, (theme.SCREEN_W // 2 - ls.get_width() // 2, head_h + 150 + i * 34))

    def _render_gps_detail(self, surf, head_h) -> None:
        fix = self.gps.latest()
        pw, ph = 360, 240
        px = (theme.SCREEN_W - pw) // 2
        py = head_h + 40
        pygame.draw.rect(surf, (10, 10, 20), (px, py, pw, ph), border_radius=10)
        pygame.draw.rect(surf, theme.ACCENT, (px, py, pw, ph), 2, border_radius=10)
        f_med = pygame.font.Font(None, 28)
        f_small = pygame.font.Font(None, 22)
        surf.blit(f_med.render("GPS STATUS", True, theme.ACCENT), (px + 20, py + 15))
        pygame.draw.line(surf, theme.DIVIDER, (px + 20, py + 45), (px + pw - 20, py + 45))
        lines = [
            f"Fix: {'YES' if fix.has_fix else 'NO'}",
            f"Source: {fix.device_path}",
            f"Sats: {fix.sats}",
            f"Accuracy: {fix.accuracy_m:.1f}m",
            f"Latitude: {fix.lat:.6f}",
            f"Longitude: {fix.lon:.6f}",
            f"Altitude: {fix.alt_m:.1f}m",
            f"Speed: {fix.speed_kmh:.1f} km/h",
        ]
        for i, ln in enumerate(lines):
            surf.blit(f_small.render(ln, True, theme.FG), (px + 30, py + 60 + i * 20))
