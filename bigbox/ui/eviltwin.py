"""Evil Twin / Captive Portal — rogue AP UI.

Wraps `bigbox/eviltwin.py` (orchestrator) and `bigbox/captive_portal.py`
(HTTP server) in a phased view:

  PICK_IFACE     — list AP-capable wlan ifaces (filtered via iw list)
  PICK_TARGET    — start passive scan; A picks an SSID, X opens the
                   on-screen keyboard for a manual SSID
  CONFIRM        — red modal warning that authorized targets only
  RUNNING        — clients connected, creds captured, uptime, hostapd
                   liveness
  STOPPED        — final summary, A starts a new session

On entry: hardware.ensure_wifi_managed() to recover from a previous
WifiAttackView monitor mode. On exit: the orchestrator's stop() flushes
iptables, kills hostapd/dnsmasq, hands the iface back to NetworkManager.
"""
from __future__ import annotations

import re
import subprocess
import threading
import time
from dataclasses import dataclass

import pygame

from bigbox import eviltwin as et
from bigbox import hardware, theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


PHASE_PICK_IFACE = "iface"
PHASE_PICK_TARGET = "target"
PHASE_CONFIRM = "confirm"
PHASE_RUNNING = "running"
PHASE_STOPPED = "stopped"


@dataclass
class _ApScan:
    bssid: str
    ssid: str
    channel: int
    signal: int


def _scan_aps(iface: str) -> list[_ApScan]:
    """One-shot `iw dev <iface> scan` parser. Returns a deduped list."""
    try:
        proc = subprocess.run(
            ["iw", "dev", iface, "scan"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, text=True, timeout=15,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []

    aps: dict[str, _ApScan] = {}
    bssid = ssid = ""
    channel = 0
    signal = -100

    def commit():
        nonlocal bssid, ssid, channel, signal
        if bssid and ssid:
            existing = aps.get(ssid)
            if not existing or signal > existing.signal:
                aps[ssid] = _ApScan(bssid=bssid, ssid=ssid,
                                    channel=channel, signal=signal)
        bssid = ssid = ""
        channel = 0
        signal = -100

    for raw in proc.stdout.splitlines():
        line = raw.rstrip()
        m = re.match(r"^BSS\s+([0-9a-fA-F:]{17})", line)
        if m:
            commit()
            bssid = m.group(1).lower()
            continue
        s = line.strip()
        if s.startswith("SSID:"):
            ssid = s.split(":", 1)[1].strip()
        elif s.startswith("freq:"):
            try:
                f = int(s.split(":", 1)[1].strip())
                channel = _freq_to_channel(f)
            except ValueError:
                pass
        elif s.startswith("signal:"):
            m2 = re.search(r"-?\d+(\.\d+)?", s)
            if m2:
                try:
                    signal = int(float(m2.group(0)))
                except ValueError:
                    pass
    commit()

    return sorted(aps.values(), key=lambda a: a.signal, reverse=True)


def _freq_to_channel(freq: int) -> int:
    if 2412 <= freq <= 2484:
        return 14 if freq == 2484 else (freq - 2407) // 5
    if 5170 <= freq <= 5825:
        return (freq - 5000) // 5
    return 0


class EvilTwinView:
    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_IFACE
        self.status_msg = "Pick an AP-capable adapter"

        # Recover from previous tools (monitor mode, leftover hostapd, etc.)
        hardware.ensure_wifi_managed()

        # Filter ifaces for AP capability — avoids users picking an iface
        # that hostapd will fail on three steps later.
        all_clients = hardware.list_wifi_clients() or ["wlan0"]
        self.ifaces = [i for i in all_clients if et.iface_supports_ap(i)]
        if not self.ifaces:
            # Show all of them anyway; the user will see the failure
            # surfaced by hostapd.
            self.ifaces = all_clients
        self.iface_cursor = 0

        # Scan target state
        self.aps: list[_ApScan] = []
        self.ap_cursor = 0
        self.ap_scroll = 0
        self.scan_in_flight = False
        self._scan_thread: threading.Thread | None = None

        # Selected target
        self.selected_iface: str | None = None
        self.selected_ssid: str | None = None
        self.selected_channel: int = 6

        # Running session
        self.session: et.EvilTwinSession | None = None
        self.error_msg: str = ""

    # ---------- helpers ----------
    def _start_scan(self) -> None:
        if not self.selected_iface or self.scan_in_flight:
            return
        iface = self.selected_iface
        self.scan_in_flight = True
        self.status_msg = f"Scanning on {iface}..."

        def _worker():
            results = _scan_aps(iface)
            self.aps = results
            self.scan_in_flight = False
            self.status_msg = (f"{len(results)} SSIDs"
                               if results else "no SSIDs visible")

        self._scan_thread = threading.Thread(target=_worker, daemon=True)
        self._scan_thread.start()

    def _open_manual_ssid(self, ctx: SectionContext) -> None:
        def _on_input(val: str | None):
            if val:
                self.selected_ssid = val.strip()
                self.selected_channel = 6
                self.phase = PHASE_CONFIRM
        ctx.get_input("Target SSID (manual)", _on_input)

    def _start_session(self) -> None:
        if not self.selected_iface or not self.selected_ssid:
            return
        self.status_msg = "Starting AP..."
        self.session = et.EvilTwinSession(
            iface=self.selected_iface,
            ssid=self.selected_ssid,
            channel=self.selected_channel or 6,
        )

        def _worker():
            ok, msg = self.session.start() if self.session else (False, "no session")
            if ok:
                self.status_msg = msg
                self.phase = PHASE_RUNNING
            else:
                self.error_msg = msg
                self.status_msg = msg
                self.phase = PHASE_STOPPED

        threading.Thread(target=_worker, daemon=True).start()

    def _stop_session(self) -> None:
        if self.session:
            try:
                self.session.stop()
            except Exception:
                pass
        self.status_msg = "Stopped"
        self.phase = PHASE_STOPPED

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
                self.selected_iface = self.ifaces[self.iface_cursor]
                self.phase = PHASE_PICK_TARGET
                self._start_scan()
            return

        if self.phase == PHASE_PICK_TARGET:
            if ev.button is Button.B:
                self.phase = PHASE_PICK_IFACE
                self.selected_iface = None
                self.aps = []
                return
            if ev.button is Button.X:
                if self.scan_in_flight:
                    return
                self._start_scan()
                return
            if ev.button is Button.Y:
                # Manual SSID via on-screen keyboard
                self._open_manual_ssid(ctx)
                return
            if not self.aps:
                return
            if ev.button is Button.UP:
                self.ap_cursor = (self.ap_cursor - 1) % len(self.aps)
                self._adjust_scroll()
            elif ev.button is Button.DOWN:
                self.ap_cursor = (self.ap_cursor + 1) % len(self.aps)
                self._adjust_scroll()
            elif ev.button is Button.A:
                ap = self.aps[self.ap_cursor]
                self.selected_ssid = ap.ssid
                self.selected_channel = ap.channel or 6
                self.phase = PHASE_CONFIRM
            return

        if self.phase == PHASE_CONFIRM:
            if ev.button is Button.A:
                self._start_session()
            elif ev.button is Button.B:
                self.phase = PHASE_PICK_TARGET
            return

        if self.phase == PHASE_RUNNING:
            if ev.button is Button.B:
                self._stop_session()
            return

        if self.phase == PHASE_STOPPED:
            if ev.button is Button.B:
                self.dismissed = True
            elif ev.button is Button.A:
                # Spin up another session against the same target
                self.error_msg = ""
                if self.selected_ssid and self.selected_iface:
                    self._start_session()
            return

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
        surf.blit(f_title.render("WIRELESS :: EVIL_TWIN", True, theme.ACCENT),
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
        surf.blit(h_surf, (theme.SCREEN_W - h_surf.get_width() - theme.PADDING,
                           theme.SCREEN_H - foot_h + 8))

        if self.phase == PHASE_PICK_IFACE:
            self._render_iface(surf, head_h)
        elif self.phase == PHASE_PICK_TARGET:
            self._render_target(surf, head_h, foot_h)
        elif self.phase == PHASE_CONFIRM:
            self._render_target(surf, head_h, foot_h)  # backdrop
            self._render_confirm(surf)
        elif self.phase == PHASE_RUNNING:
            self._render_running(surf, head_h, foot_h)
        elif self.phase == PHASE_STOPPED:
            self._render_stopped(surf, head_h, foot_h)

    def _hint(self) -> str:
        if self.phase == PHASE_PICK_IFACE:
            return "A: Use  B: Back"
        if self.phase == PHASE_PICK_TARGET:
            return "A: Target  X: Rescan  Y: Manual  B: Back"
        if self.phase == PHASE_CONFIRM:
            return "A: I am authorized  B: Cancel"
        if self.phase == PHASE_RUNNING:
            return "B: Stop"
        if self.phase == PHASE_STOPPED:
            return "A: Run again  B: Back"
        return "B: Back"

    def _render_iface(self, surf: pygame.Surface, head_h: int) -> None:
        f = pygame.font.Font(None, 28)
        f_small = pygame.font.Font(None, 22)
        title = f.render("Pick an AP-capable wireless adapter", True, theme.FG)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2, head_h + 16))

        warn = f_small.render(
            "The adapter loses internet while running. Use a 2nd USB Wi-Fi to stay online.",
            True, theme.FG_DIM)
        surf.blit(warn, (theme.SCREEN_W // 2 - warn.get_width() // 2, head_h + 50))

        if not self.ifaces:
            err = f_small.render("No wlan interfaces found.", True, theme.ERR)
            surf.blit(err, (theme.SCREEN_W // 2 - err.get_width() // 2,
                            theme.SCREEN_H // 2))
            return

        list_y = head_h + 90
        for i, name in enumerate(self.ifaces):
            sel = i == self.iface_cursor
            rect = pygame.Rect(theme.SCREEN_W // 2 - 200, list_y + i * 50, 400, 44)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=5)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=5)
            color = theme.ACCENT if sel else theme.FG
            label = f.render(name, True, color)
            surf.blit(label, (rect.x + 14, rect.y + 8))

    def _render_target(self, surf: pygame.Surface,
                       head_h: int, foot_h: int) -> None:
        list_x = theme.PADDING
        list_y = head_h + 8
        list_w = theme.SCREEN_W - 2 * theme.PADDING
        list_h = theme.SCREEN_H - head_h - foot_h - 16
        pygame.draw.rect(surf, (5, 5, 10), (list_x, list_y, list_w, list_h))
        pygame.draw.rect(surf, theme.DIVIDER, (list_x, list_y, list_w, list_h), 1)

        if not self.aps:
            f = pygame.font.Font(None, 24)
            msg = ("Scanning..." if self.scan_in_flight
                   else "No SSIDs. X to rescan, Y for manual entry.")
            ts = f.render(msg, True, theme.FG_DIM)
            surf.blit(ts, (theme.SCREEN_W // 2 - ts.get_width() // 2,
                           theme.SCREEN_H // 2 - 12))
            return

        row_h = 40
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
            label = f_main.render(ap.ssid or "<hidden>", True, color)
            surf.blit(label, (rect.x + 10, rect.y + 4))
            meta = f"{ap.bssid}  ch{ap.channel}  {ap.signal}dBm"
            ms = f_meta.render(meta, True, theme.FG_DIM)
            surf.blit(ms, (rect.x + 10, rect.y + 22))

        # Scroll indicator
        if len(self.aps) > visible:
            bar_x = list_x + list_w - 4
            thumb_h = max(20, int(list_h * visible / len(self.aps)))
            thumb_y = list_y + int(list_h * self.ap_scroll / len(self.aps))
            pygame.draw.rect(surf, theme.DIVIDER, (bar_x, list_y, 3, list_h))
            pygame.draw.rect(surf, theme.ACCENT, (bar_x, thumb_y, 3, thumb_h))

    def _render_confirm(self, surf: pygame.Surface) -> None:
        box_w, box_h = 600, 240
        bx = (theme.SCREEN_W - box_w) // 2
        by = (theme.SCREEN_H - box_h) // 2
        overlay = pygame.Surface((theme.SCREEN_W, theme.SCREEN_H),
                                 pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 220))
        surf.blit(overlay, (0, 0))
        pygame.draw.rect(surf, theme.BG, (bx, by, box_w, box_h), border_radius=10)
        pygame.draw.rect(surf, theme.ERR, (bx, by, box_w, box_h), 2, border_radius=10)

        f_title = pygame.font.Font(None, 30)
        f_body = pygame.font.Font(None, 22)
        title = f_title.render("CONFIRM: ROGUE AP", True, theme.ERR)
        surf.blit(title, (bx + box_w // 2 - title.get_width() // 2, by + 16))

        target = f_body.render(
            f"SSID: {self.selected_ssid}   ch{self.selected_channel}   on {self.selected_iface}",
            True, theme.FG)
        surf.blit(target, (bx + box_w // 2 - target.get_width() // 2, by + 56))

        msgs = [
            "This will impersonate the target SSID, capture connecting",
            "clients via DHCP, and present a fake login page that logs",
            "credentials to loot/captive/. Authorized targets only —",
            "running this against networks you don't own is illegal.",
        ]
        for i, msg in enumerate(msgs):
            ms = f_body.render(msg, True, theme.FG_DIM)
            surf.blit(ms, (bx + 24, by + 96 + i * 24))

        prompt = f_body.render("A: I am authorized  B: Cancel",
                               True, theme.ACCENT)
        surf.blit(prompt, (bx + box_w // 2 - prompt.get_width() // 2,
                           by + box_h - 30))

    def _render_running(self, surf: pygame.Surface,
                        head_h: int, foot_h: int) -> None:
        sess = self.session
        if not sess:
            return
        running = sess.is_running()
        f_huge = pygame.font.Font(None, 80)
        f_med = pygame.font.Font(None, 26)
        f_small = pygame.font.Font(None, 20)

        # SSID banner
        banner = f_med.render(
            f"AP: {sess.ssid}  iface {sess.iface}  ch{sess.channel}",
            True, theme.ACCENT if running else theme.ERR)
        surf.blit(banner, (theme.PADDING, head_h + 12))

        # Big counters
        cy = head_h + 60
        col_w = theme.SCREEN_W // 2

        clients_n = f_huge.render(str(sess.clients_connected()),
                                  True, theme.ACCENT)
        surf.blit(clients_n, (col_w // 2 - clients_n.get_width() // 2, cy))
        clients_l = f_med.render("CLIENTS", True, theme.FG_DIM)
        surf.blit(clients_l, (col_w // 2 - clients_l.get_width() // 2,
                              cy + clients_n.get_height() + 4))

        creds_n = f_huge.render(str(sess.creds_captured()), True, theme.WARN)
        surf.blit(creds_n, (col_w + col_w // 2 - creds_n.get_width() // 2, cy))
        creds_l = f_med.render("CREDS", True, theme.FG_DIM)
        surf.blit(creds_l, (col_w + col_w // 2 - creds_l.get_width() // 2,
                            cy + creds_n.get_height() + 4))

        # Last creds preview (sanitised)
        if sess.portal and sess.portal.last_creds:
            last = sess.portal.last_creds
            email = last.get("email", "")
            pw_len = len(last.get("password", ""))
            preview = f"last: {email}  password({pw_len})"
            ps = f_small.render(preview[:90], True, theme.WARN)
            surf.blit(ps, (theme.PADDING, theme.SCREEN_H - foot_h - 56))

        # Uptime + portal path
        info = f"uptime {sess.uptime_s()}s   portal: 192.168.45.1:80"
        info_s = f_small.render(info, True, theme.FG_DIM)
        surf.blit(info_s, (theme.PADDING, theme.SCREEN_H - foot_h - 32))

        if not running:
            warn = f_med.render("hostapd or dnsmasq exited — see logs",
                                True, theme.ERR)
            surf.blit(warn, (theme.SCREEN_W // 2 - warn.get_width() // 2,
                             theme.SCREEN_H - foot_h - 88))

    def _render_stopped(self, surf: pygame.Surface,
                        head_h: int, foot_h: int) -> None:
        f_big = pygame.font.Font(None, 36)
        f_med = pygame.font.Font(None, 22)
        title = "STOPPED" if not self.error_msg else "FAILED"
        color = theme.ACCENT if not self.error_msg else theme.ERR
        ts = f_big.render(title, True, color)
        surf.blit(ts, (theme.SCREEN_W // 2 - ts.get_width() // 2, head_h + 30))

        if self.session:
            wifi_count = self.session.creds_captured()
            client_count = self.session.clients_connected()
            lines = [
                f"SSID:    {self.session.ssid}",
                f"Iface:   {self.session.iface}",
                f"Clients: {client_count}",
                f"Creds:   {wifi_count}",
            ]
        else:
            lines = []

        if self.error_msg:
            lines.append("")
            for chunk in [self.error_msg[i:i + 70]
                          for i in range(0, len(self.error_msg), 70)][:3]:
                lines.append(chunk)

        lines.append("")
        lines.append("Captures saved to loot/captive/.")

        for i, ln in enumerate(lines):
            color = theme.ERR if "FAILED" in ln else (
                theme.FG if not ln.startswith(("Captures",)) else theme.FG_DIM)
            ls = f_med.render(ln, True, color)
            surf.blit(ls, (theme.SCREEN_W // 2 - ls.get_width() // 2,
                           head_h + 90 + i * 26))
