"""Three lightweight wireless attacks that share a monitor-mode lifecycle.

  ProbeSnifferView   — passive: tcpdump probe-req frames, list (client MAC,
                       SSID) pairs. Reveals every nearby phone's known
                       networks (work SSIDs, home, hotels, …). Doesn't
                       transmit.
  BeaconFloodView    — active: mdk4 b -f <ssid file> -s 200. Floods 200
                       fake SSIDs into nearby devices' network lists.
  KarmaLiteView      — active: combines the two. Captures probe-req
                       SSIDs into a file, restarts mdk4 every ~10 s so
                       it beacon-floods those exact SSIDs. Each phone
                       in range sees "its" home network appearing.

All three share an iface-picker → airmon-ng start → run → cleanup
lifecycle. On entry they call hardware.ensure_wifi_managed() to recover
from a previous tool that left an interface in a weird state. On exit
they kill subprocesses + airmon-ng stop the *mon iface.

Authorized targets only — beacon flood + karma transmit on shared
spectrum and are illegal where you're not the operator. The view does
not pop a confirm modal because the section description already names
them as attacks.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pygame

from bigbox import hardware, theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


PHASE_PICK_IFACE = "iface"
PHASE_ENABLING = "enabling"
PHASE_RUNNING = "running"
PHASE_STOPPED = "stopped"


# Default beacon-flood SSID list — fun + recognisable so demos pop.
DEFAULT_FLOOD_SSIDS = [
    "FREE_PUBLIC_WIFI", "starbucks", "Hilton_Lobby", "MarriottWiFi",
    "ATT-WIFI", "xfinitywifi", "UNITED_Wi-Fi", "DELTA_WiFi",
    "GuestNetwork", "office", "Home", "Linksys",
    "NETGEAR", "TP-LINK_5G", "iPhone", "Pixel-7",
    "FBI_Surveillance_Van", "NSA_Mobile_Unit", "DHS-Op-7",
    "DontConnect", "DefinitelyNotMalware", "PRISM",
    "DROP_TABLE_users", "haxx0r", "ssh:nope",
    "Local Bar Wi-Fi", "AirportWiFi", "Hotel_5G",
]

DEFAULT_FLOOD_PATH = Path("/tmp/bigbox-flood-ssids.txt")
KARMA_SSID_PATH = Path("/tmp/bigbox-karma-ssids.txt")


# --------------------------------------------------------------------------
# Common base — iface pick + airmon-ng lifecycle + drawing helpers.
# --------------------------------------------------------------------------

class _MonitorModeView:
    """Shared scaffold for the three lite-attack views.

    Subclasses set self.title, then override:
      _start_run()  : spin up the attack subprocesses; set status_msg
      _stop_run()   : kill subprocesses
      _render_run() : paint the running phase contents
    """

    title: str = "WIRELESS :: TOOL"

    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_IFACE
        self.status_msg = "Pick a monitor-capable adapter"
        self.error_msg: str = ""

        # Recover from any previous monitor-mode session
        hardware.ensure_wifi_managed()

        # Filter the picker to interfaces that actually support monitor
        # mode. Pi 4's onboard wlan0 (BCM43455 sans nexmon) doesn't —
        # showing it as an option just sets the user up for failure.
        monitor_caps = hardware.list_monitor_capable_clients()
        if monitor_caps:
            self.ifaces = monitor_caps
        else:
            # No monitor-capable adapter visible — show whatever's there
            # so the user can see the picker isn't broken, plus a hint.
            self.ifaces = hardware.list_wifi_clients() or ["wlan0"]
            self.status_msg = ("no monitor-capable adapter detected — "
                               "plug in an Alfa or similar")
        self.iface_cursor = 0
        self.selected_iface: str | None = None
        self.mon_iface: str | None = None

    # ---------- airmon-ng helpers ----------
    def _enable_monitor(self, iface: str) -> bool:
        # hardware.enable_monitor() handles NM detach, airmon-ng start,
        # multi-format output parsing, AND a manual-flip fallback for
        # adapters where airmon-ng can't create a *mon vif but can
        # switch the existing iface in place.
        mon = hardware.enable_monitor(iface)
        if not mon:
            self.status_msg = ("monitor mode failed — adapter may not "
                               "support it (try a different one)")
            return False
        self.mon_iface = mon
        return True

    def _disable_monitor(self) -> None:
        if not self.mon_iface:
            return
        try:
            subprocess.run(
                ["airmon-ng", "stop", self.mon_iface],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, timeout=10,
            )
        except Exception:
            pass
        self.mon_iface = None
        # Hand the iface back to NetworkManager
        hardware.ensure_wifi_managed(self.selected_iface)

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self._shutdown()
            return

        if self.phase == PHASE_PICK_IFACE:
            if not self.ifaces:
                return
            if ev.button is Button.UP:
                self.iface_cursor = (self.iface_cursor - 1) % len(self.ifaces)
            elif ev.button is Button.DOWN:
                self.iface_cursor = (self.iface_cursor + 1) % len(self.ifaces)
            elif ev.button is Button.A:
                self.selected_iface = self.ifaces[self.iface_cursor]
                self.phase = PHASE_ENABLING
                self.status_msg = f"airmon-ng start {self.selected_iface}..."
                threading.Thread(target=self._enable_then_run,
                                 daemon=True).start()
            return

        if self.phase == PHASE_RUNNING:
            if ev.button is Button.A:
                self._stop_run()
                self.phase = PHASE_STOPPED
                self.status_msg = "Stopped — A to restart, B to back out"
            return

        if self.phase == PHASE_STOPPED:
            if ev.button is Button.A and self.mon_iface:
                self.phase = PHASE_RUNNING
                self._start_run()
            return

    def _enable_then_run(self) -> None:
        if not self.selected_iface:
            return
        if not self._enable_monitor(self.selected_iface):
            self.phase = PHASE_PICK_IFACE
            return
        self.phase = PHASE_RUNNING
        self._start_run()

    def _shutdown(self) -> None:
        try:
            self._stop_run()
        except Exception:
            pass
        try:
            self._disable_monitor()
        except Exception:
            pass
        self.dismissed = True

    # ---------- subclass hooks (default no-ops) ----------
    def _start_run(self) -> None: ...
    def _stop_run(self) -> None: ...
    def _render_run(self, surf: pygame.Surface, body_rect: pygame.Rect) -> None: ...

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render(self.title, True, theme.ACCENT),
                  (theme.PADDING, 8))

        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20),
                         (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER,
                         (0, theme.SCREEN_H - foot_h),
                         (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        f_small = pygame.font.Font(None, 20)
        s_surf = f_small.render(self.status_msg[:80], True, theme.ACCENT)
        surf.blit(s_surf, (theme.PADDING, theme.SCREEN_H - foot_h + 8))
        h_surf = f_small.render(self._hint(), True, theme.FG_DIM)
        surf.blit(h_surf,
                  (theme.SCREEN_W - h_surf.get_width() - theme.PADDING,
                   theme.SCREEN_H - foot_h + 8))

        body = pygame.Rect(theme.PADDING, head_h + 8,
                           theme.SCREEN_W - 2 * theme.PADDING,
                           theme.SCREEN_H - head_h - foot_h - 16)

        if self.phase == PHASE_PICK_IFACE:
            self._render_iface(surf, body)
        elif self.phase == PHASE_ENABLING:
            self._render_centered(surf, "Enabling monitor mode...")
        elif self.phase in (PHASE_RUNNING, PHASE_STOPPED):
            self._render_run(surf, body)

    def _hint(self) -> str:
        if self.phase == PHASE_PICK_IFACE:
            return "A: Use  B: Back"
        if self.phase == PHASE_RUNNING:
            return "A: Stop  B: Back"
        if self.phase == PHASE_STOPPED:
            return "A: Restart  B: Back"
        return "B: Back"

    def _render_iface(self, surf: pygame.Surface, body: pygame.Rect) -> None:
        f = pygame.font.Font(None, 28)
        f_small = pygame.font.Font(None, 22)
        title = f.render("Pick wireless adapter", True, theme.FG)
        surf.blit(title, (body.centerx - title.get_width() // 2, body.y + 10))
        sub = f_small.render(
            "Adapter must support monitor mode (Alfa, etc.)",
            True, theme.FG_DIM)
        surf.blit(sub, (body.centerx - sub.get_width() // 2, body.y + 44))

        if not self.ifaces:
            err = f_small.render("No wlan interfaces found.", True, theme.ERR)
            surf.blit(err, (body.centerx - err.get_width() // 2, body.centery))
            return
        list_y = body.y + 90
        for i, name in enumerate(self.ifaces):
            sel = i == self.iface_cursor
            rect = pygame.Rect(body.centerx - 200, list_y + i * 50, 400, 44)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=5)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=5)
            color = theme.ACCENT if sel else theme.FG
            label = f.render(name, True, color)
            surf.blit(label, (rect.x + 14, rect.y + 8))

    def _render_centered(self, surf: pygame.Surface, text: str) -> None:
        f = pygame.font.Font(None, 30)
        s = f.render(text, True, theme.FG)
        surf.blit(s, (theme.SCREEN_W // 2 - s.get_width() // 2,
                      theme.SCREEN_H // 2 - 16))


# --------------------------------------------------------------------------
# Probe sniffer
# --------------------------------------------------------------------------

@dataclass
class _Probe:
    mac: str
    ssid: str
    last_ts: float = field(default_factory=time.time)
    count: int = 1


class ProbeSnifferView(_MonitorModeView):
    title = "WIRELESS :: PROBE_SNIFF"

    def __init__(self) -> None:
        super().__init__()
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        # keyed by (mac, ssid) so a single phone broadcasting many SSIDs
        # gets one row per SSID
        self.probes: dict[tuple[str, str], _Probe] = {}
        self.total_frames = 0

    def _start_run(self) -> None:
        self.status_msg = f"Sniffing probes on {self.mon_iface}"
        if not shutil.which("tcpdump"):
            self.error_msg = "tcpdump not installed"
            self.status_msg = self.error_msg
            return
        cmd = [
            "tcpdump", "-i", self.mon_iface, "-e", "-l", "-n",
            "-s", "256",
            "type", "mgt", "subtype", "probe-req",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
                preexec_fn=os.setsid,
            )
        except Exception as e:
            self.error_msg = f"tcpdump: {e}"
            self.status_msg = self.error_msg
            self._proc = None
            return
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def _stop_run(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    # tcpdump probe-req line shape:
    # "12:34:56.789012 12345us tsft  1.0 Mb/s 2412 MHz 11g -73dBm signal -99dBm noise antenna 1
    #   BSSID:Broadcast DA:Broadcast SA:aa:bb:cc:dd:ee:ff (oui Apple) Probe Request (HomeWiFi) [...]"
    _RE_SA = re.compile(r"SA:([0-9A-Fa-f:]{17})")
    _RE_SSID = re.compile(r"Probe Request \(([^)]*)\)")

    def _reader_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        for line in self._proc.stdout:
            self.total_frames += 1
            sa = self._RE_SA.search(line)
            ssid_m = self._RE_SSID.search(line)
            if not sa or not ssid_m:
                continue
            mac = sa.group(1).lower()
            ssid = ssid_m.group(1).strip()
            if not ssid:
                continue  # broadcast probe (no specific SSID asked)
            key = (mac, ssid)
            now = time.time()
            existing = self.probes.get(key)
            if existing:
                existing.count += 1
                existing.last_ts = now
            else:
                self.probes[key] = _Probe(mac=mac, ssid=ssid, last_ts=now)

    def _render_run(self, surf: pygame.Surface, body: pygame.Rect) -> None:
        f_big = pygame.font.Font(None, 48)
        f_med = pygame.font.Font(None, 22)
        f_small = pygame.font.Font(None, 18)

        # Counters
        unique_macs = len({m for m, _ in self.probes.keys()})
        unique_ssids = len({s for _, s in self.probes.keys()})
        n_left = f_big.render(str(unique_macs), True, theme.ACCENT)
        l_left = f_small.render("DEVICES", True, theme.FG_DIM)
        surf.blit(n_left, (body.x + 50, body.y + 6))
        surf.blit(l_left, (body.x + 50, body.y + 6 + n_left.get_height() + 2))

        n_mid = f_big.render(str(unique_ssids), True, theme.ACCENT)
        l_mid = f_small.render("SSIDS PROBED", True, theme.FG_DIM)
        surf.blit(n_mid, (body.x + 230, body.y + 6))
        surf.blit(l_mid, (body.x + 230, body.y + 6 + n_mid.get_height() + 2))

        n_r = f_big.render(str(self.total_frames), True, theme.WARN)
        l_r = f_small.render("FRAMES", True, theme.FG_DIM)
        surf.blit(n_r, (body.x + 470, body.y + 6))
        surf.blit(l_r, (body.x + 470, body.y + 6 + n_r.get_height() + 2))

        # Recent probes list
        list_y = body.y + 90
        list_h = body.height - 90
        pygame.draw.rect(surf, (5, 5, 10),
                         (body.x, list_y, body.width, list_h))
        pygame.draw.rect(surf, theme.DIVIDER,
                         (body.x, list_y, body.width, list_h), 1)

        recent = sorted(self.probes.values(),
                        key=lambda p: p.last_ts, reverse=True)
        row_h = 22
        for i, p in enumerate(recent[: max(1, list_h // row_h)]):
            ago = max(0, int(time.time() - p.last_ts))
            line = f"{p.mac}  {p.ssid[:42]:42}  ×{p.count:<4}  {ago}s ago"
            ls = f_small.render(line, True, theme.FG_DIM)
            surf.blit(ls, (body.x + 8, list_y + 6 + i * row_h))


# --------------------------------------------------------------------------
# Beacon flood
# --------------------------------------------------------------------------

class BeaconFloodView(_MonitorModeView):
    title = "WIRELESS :: BEACON_FLOOD"

    def __init__(self) -> None:
        super().__init__()
        self._proc: subprocess.Popen | None = None
        self.start_time: float = 0.0
        # Pre-write the SSID file so the kick-off is instant.
        try:
            DEFAULT_FLOOD_PATH.write_text("\n".join(DEFAULT_FLOOD_SSIDS) + "\n")
        except Exception:
            pass

    def _start_run(self) -> None:
        if not shutil.which("mdk4"):
            self.error_msg = "mdk4 not installed"
            self.status_msg = self.error_msg
            return
        if not self.mon_iface:
            return
        cmd = [
            "mdk4", self.mon_iface, "b",
            "-f", str(DEFAULT_FLOOD_PATH),
            "-s", "200",            # frames/sec/SSID
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            self.start_time = time.time()
            self.status_msg = (f"Beacon flood: {len(DEFAULT_FLOOD_SSIDS)} "
                               f"SSIDs @ 200 fps each")
        except Exception as e:
            self.error_msg = f"mdk4: {e}"
            self.status_msg = self.error_msg
            self._proc = None

    def _stop_run(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def _render_run(self, surf: pygame.Surface, body: pygame.Rect) -> None:
        f_huge = pygame.font.Font(None, 80)
        f_med = pygame.font.Font(None, 26)
        f_small = pygame.font.Font(None, 20)

        running = self._proc is not None and self._proc.poll() is None
        center_x = body.centerx
        cy = body.y + 30

        big = f_huge.render(
            str(len(DEFAULT_FLOOD_SSIDS)) if running else "0",
            True, theme.ACCENT if running else theme.ERR)
        surf.blit(big, (center_x - big.get_width() // 2, cy))
        sub = f_med.render("SSIDs flooding @ 200 fps each",
                           True, theme.FG_DIM)
        surf.blit(sub, (center_x - sub.get_width() // 2,
                        cy + big.get_height() + 4))

        # Sample of what's being broadcast
        list_y = body.y + 200
        f_mono = pygame.font.Font(None, 18)
        cap = (body.height - 200) // 18
        for i, ssid in enumerate(DEFAULT_FLOOD_SSIDS[: cap]):
            ls = f_mono.render(ssid, True, theme.FG_DIM)
            col = i % 2
            row = i // 2
            x = body.x + 30 + col * 380
            surf.blit(ls, (x, list_y + row * 18))

        # Uptime
        if running:
            up = int(time.time() - self.start_time)
            us = f_small.render(f"uptime {up}s", True, theme.FG_DIM)
            surf.blit(us, (body.x + 8, body.y + body.height - 22))


# --------------------------------------------------------------------------
# Karma-lite
# --------------------------------------------------------------------------

class KarmaLiteView(_MonitorModeView):
    title = "WIRELESS :: KARMA_LITE"

    REFRESH_SECONDS = 10  # how often to re-launch mdk4 with updated SSIDs

    def __init__(self) -> None:
        super().__init__()
        self._tcpdump: subprocess.Popen | None = None
        self._mdk4: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._cycle_thread: threading.Thread | None = None
        self.stop_flag = False
        self.start_time: float = 0.0

        # Live state — observed SSIDs phones are probing for
        self.captured_ssids: dict[str, float] = {}  # ssid -> last seen
        self.unique_macs: set[str] = set()
        self.total_frames = 0
        self.cycles = 0

        # Seed with the default flood list so we always have *something* to
        # broadcast even before any client probes.
        for s in DEFAULT_FLOOD_SSIDS[:10]:
            self.captured_ssids[s] = time.time()

    # ---------- subprocess helpers ----------
    def _kill(self, proc_attr: str) -> None:
        proc: subprocess.Popen | None = getattr(self, proc_attr)
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        setattr(self, proc_attr, None)

    def _launch_tcpdump(self) -> None:
        cmd = [
            "tcpdump", "-i", self.mon_iface, "-e", "-l", "-n",
            "-s", "256",
            "type", "mgt", "subtype", "probe-req",
        ]
        self._tcpdump = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
            preexec_fn=os.setsid,
        )

    def _launch_mdk4(self) -> None:
        # Write current SSID set out for mdk4 to read at launch
        try:
            ssids = list(self.captured_ssids.keys())[:120]
            KARMA_SSID_PATH.write_text("\n".join(ssids) + "\n")
        except Exception:
            return
        cmd = [
            "mdk4", self.mon_iface, "b",
            "-f", str(KARMA_SSID_PATH),
            "-s", "200",
        ]
        self._mdk4 = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )

    # ---------- run / stop ----------
    def _start_run(self) -> None:
        if not shutil.which("tcpdump") or not shutil.which("mdk4"):
            self.error_msg = "tcpdump or mdk4 missing"
            self.status_msg = self.error_msg
            return
        if not self.mon_iface:
            return

        self.stop_flag = False
        self.start_time = time.time()

        try:
            self._launch_tcpdump()
        except Exception as e:
            self.error_msg = f"tcpdump: {e}"
            self.status_msg = self.error_msg
            return

        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True)
        self._reader_thread.start()

        self._launch_mdk4()
        self._cycle_thread = threading.Thread(
            target=self._cycle_loop, daemon=True)
        self._cycle_thread.start()

        self.status_msg = "Karma-lite: probing for nearby phones..."

    def _stop_run(self) -> None:
        self.stop_flag = True
        self._kill("_mdk4")
        self._kill("_tcpdump")

    # ---------- worker loops ----------
    _RE_SA = re.compile(r"SA:([0-9A-Fa-f:]{17})")
    _RE_SSID = re.compile(r"Probe Request \(([^)]*)\)")

    def _reader_loop(self) -> None:
        if not self._tcpdump or not self._tcpdump.stdout:
            return
        for line in self._tcpdump.stdout:
            if self.stop_flag:
                break
            self.total_frames += 1
            sa = self._RE_SA.search(line)
            ssid_m = self._RE_SSID.search(line)
            if not ssid_m:
                continue
            ssid = ssid_m.group(1).strip()
            if not ssid:
                continue
            self.captured_ssids[ssid] = time.time()
            if sa:
                self.unique_macs.add(sa.group(1).lower())

    def _cycle_loop(self) -> None:
        while not self.stop_flag:
            time.sleep(self.REFRESH_SECONDS)
            if self.stop_flag:
                break
            self._kill("_mdk4")
            try:
                self._launch_mdk4()
                self.cycles += 1
            except Exception:
                pass

    # ---------- render ----------
    def _render_run(self, surf: pygame.Surface, body: pygame.Rect) -> None:
        f_huge = pygame.font.Font(None, 64)
        f_med = pygame.font.Font(None, 22)
        f_small = pygame.font.Font(None, 18)

        cy = body.y + 6
        col_w = body.width // 3

        cnt_ssids = len(self.captured_ssids)
        cnt_macs = len(self.unique_macs)
        cnt_cycles = self.cycles

        for i, (n, label, color) in enumerate([
            (cnt_ssids, "SSIDS BAITED", theme.ACCENT),
            (cnt_macs, "DEVICES SEEN", theme.WARN),
            (cnt_cycles, "REBROADCAST", theme.FG_DIM),
        ]):
            ns = f_huge.render(str(n), True, color)
            ls = f_small.render(label, True, theme.FG_DIM)
            cx = body.x + col_w * i + col_w // 2
            surf.blit(ns, (cx - ns.get_width() // 2, cy))
            surf.blit(ls, (cx - ls.get_width() // 2,
                           cy + ns.get_height() + 2))

        # Recent SSIDs being broadcast (newest first)
        list_y = body.y + 130
        list_h = body.height - 130
        pygame.draw.rect(surf, (5, 5, 10),
                         (body.x, list_y, body.width, list_h))
        pygame.draw.rect(surf, theme.DIVIDER,
                         (body.x, list_y, body.width, list_h), 1)

        recent = sorted(self.captured_ssids.items(),
                        key=lambda kv: kv[1], reverse=True)
        row_h = 20
        cap = max(1, list_h // row_h)
        for i, (ssid, ts) in enumerate(recent[:cap]):
            ago = max(0, int(time.time() - ts))
            ls = f_small.render(f"{ssid[:54]:54}  {ago}s",
                                True, theme.FG_DIM)
            surf.blit(ls, (body.x + 8, list_y + 4 + i * row_h))
