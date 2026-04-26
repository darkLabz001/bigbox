"""Wi-Fi connect — scan APs, pick one, enter password, save the profile.

Uses NetworkManager via `nmcli` so successful connections persist as profiles
and reconnect on boot. Reuses the existing on-screen KeyboardView for password
entry by calling SectionContext.get_input().
"""
from __future__ import annotations

import math
import re
import subprocess
import threading
import time
from dataclasses import dataclass

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


@dataclass
class WifiNet:
    ssid: str
    signal: int  # 0..100
    security: str  # e.g. "WPA2", "--" for open


# Phases
PHASE_SCAN = "scan"
PHASE_LIST = "list"
PHASE_CONNECT = "connect"
PHASE_RESULT = "result"


def _unescape_nmcli(field: str) -> str:
    # `nmcli -t` escapes ':' and '\' with a backslash.
    return field.replace("\\:", ":").replace("\\\\", "\\")


def _parse_nmcli_terse(out: str) -> list[WifiNet]:
    nets: dict[str, WifiNet] = {}
    for line in out.splitlines():
        # Split on unescaped ':' — nmcli escapes literal ':' inside fields.
        parts = re.split(r'(?<!\\):', line)
        if len(parts) < 3:
            continue
        ssid = _unescape_nmcli(parts[0]).strip()
        if not ssid:
            continue
        try:
            signal = int(parts[1])
        except ValueError:
            signal = 0
        sec = _unescape_nmcli(parts[2]).strip() or "--"
        # Keep the strongest signal per SSID (avoid duplicates from multiple BSSIDs).
        if ssid not in nets or signal > nets[ssid].signal:
            nets[ssid] = WifiNet(ssid=ssid, signal=signal, security=sec)
    return sorted(nets.values(), key=lambda n: n.signal, reverse=True)


class WifiConnectView:
    """Scan, select, authenticate, save."""

    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_SCAN
        self.networks: list[WifiNet] = []
        self.cursor = 0
        self.scroll = 0
        self.status_msg = "Scanning..."
        self.selected: WifiNet | None = None
        self.result_text = ""
        self.result_ok = False
        self._scan_thread: threading.Thread | None = None
        self._connect_thread: threading.Thread | None = None
        self._waiting_for_password = False  # set while keyboard view is open

        self._start_scan()

    # ---------- nmcli helpers ----------
    def _start_scan(self) -> None:
        self.phase = PHASE_SCAN
        self.status_msg = "Scanning Wi-Fi..."
        self.networks = []
        self.cursor = 0
        self.scroll = 0
        self._scan_thread = threading.Thread(target=self._scan_worker, daemon=True)
        self._scan_thread.start()

    def _scan_worker(self) -> None:
        try:
            out = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY",
                 "dev", "wifi", "list", "--rescan", "yes"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=20,
            )
            if out.returncode != 0:
                self.status_msg = f"nmcli error: {out.stdout.strip()[:60]}"
                self.phase = PHASE_LIST  # let user press B to back out
                return
            self.networks = _parse_nmcli_terse(out.stdout)
            if not self.networks:
                self.status_msg = "No networks found"
            else:
                self.status_msg = f"{len(self.networks)} networks"
            self.phase = PHASE_LIST
        except FileNotFoundError:
            self.status_msg = "nmcli not installed"
            self.phase = PHASE_LIST
        except subprocess.TimeoutExpired:
            self.status_msg = "Scan timed out"
            self.phase = PHASE_LIST
        except Exception as e:
            self.status_msg = f"error: {type(e).__name__}"
            self.phase = PHASE_LIST

    def _start_connect(self, password: str | None) -> None:
        self._waiting_for_password = False
        if password is None:
            return  # cancelled keyboard
        net = self.selected
        if net is None:
            return
        self.phase = PHASE_CONNECT
        self.status_msg = f"Connecting to {net.ssid}..."
        self._connect_thread = threading.Thread(
            target=self._connect_worker, args=(net.ssid, password), daemon=True
        )
        self._connect_thread.start()

    def _connect_open(self) -> None:
        net = self.selected
        if net is None:
            return
        self.phase = PHASE_CONNECT
        self.status_msg = f"Connecting to {net.ssid}..."
        self._connect_thread = threading.Thread(
            target=self._connect_worker, args=(net.ssid, None), daemon=True
        )
        self._connect_thread.start()

    def _connect_worker(self, ssid: str, password: str | None) -> None:
        # bigbox runs as root on-device, so nmcli is invoked directly. In dev
        # mode (non-root) NetworkManager + polkit usually still allow this for
        # an interactive desktop user.
        cmd = ["nmcli", "dev", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        try:
            out = subprocess.run(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=45,
            )
            ok = out.returncode == 0
            text = out.stdout.strip() or ("Connected" if ok else "Connection failed")
            self.result_ok = ok
            self.result_text = text
            self.status_msg = "Connected" if ok else "Failed"
        except subprocess.TimeoutExpired:
            self.result_ok = False
            self.result_text = "Connection timed out after 45s"
            self.status_msg = "Timed out"
        except FileNotFoundError:
            self.result_ok = False
            self.result_text = "nmcli not installed"
            self.status_msg = "nmcli missing"
        except Exception as e:
            self.result_ok = False
            self.result_text = f"{type(e).__name__}: {e}"
            self.status_msg = "Error"
        finally:
            self.phase = PHASE_RESULT

    # ---------- input ----------
    def handle(self, ev: ButtonEvent, ctx: SectionContext) -> None:
        if not ev.pressed:
            return
        if self._waiting_for_password:
            return  # keyboard view owns input

        if self.phase == PHASE_SCAN:
            if ev.button is Button.B:
                self.dismissed = True
            return

        if self.phase == PHASE_LIST:
            if ev.button is Button.B:
                self.dismissed = True
            elif ev.button is Button.X:
                self._start_scan()
            elif not self.networks:
                return
            elif ev.button is Button.UP:
                self.cursor = (self.cursor - 1) % len(self.networks)
                self._adjust_scroll()
            elif ev.button is Button.DOWN:
                self.cursor = (self.cursor + 1) % len(self.networks)
                self._adjust_scroll()
            elif ev.button is Button.A:
                self.selected = self.networks[self.cursor]
                if self._is_open(self.selected):
                    self._connect_open()
                else:
                    self._waiting_for_password = True
                    ctx.get_input(
                        f"Password for {self.selected.ssid}",
                        self._start_connect,
                    )
            return

        if self.phase == PHASE_CONNECT:
            # Connection in flight; ignore input. (Could add cancel via B.)
            return

        if self.phase == PHASE_RESULT:
            if ev.button is Button.B:
                # Back to network list (keep last scan)
                self.phase = PHASE_LIST
                self.status_msg = f"{len(self.networks)} networks"
            elif ev.button is Button.A and not self.result_ok and self.selected:
                # Retry with a new password
                self._waiting_for_password = True
                ctx.get_input(
                    f"Password for {self.selected.ssid}",
                    self._start_connect,
                )

    def _is_open(self, net: WifiNet) -> bool:
        s = net.security.strip()
        return s in ("", "--", "none", "NONE")

    def _adjust_scroll(self) -> None:
        # Keep cursor within the visible window (rough — render uses same row_h).
        visible_rows = 8
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + visible_rows:
            self.scroll = self.cursor - visible_rows + 1

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        # Header
        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("SETTINGS :: WIFI_CONNECT", True, theme.ACCENT),
                  (theme.PADDING, 8))

        # Footer
        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20),
                         (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER,
                         (0, theme.SCREEN_H - foot_h),
                         (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        f_small = pygame.font.Font(None, 20)
        surf.blit(f_small.render(self.status_msg, True, theme.ACCENT),
                  (theme.PADDING, theme.SCREEN_H - foot_h + 8))

        if self.phase == PHASE_SCAN:
            self._render_scanning(surf, head_h, foot_h)
        elif self.phase == PHASE_LIST:
            self._render_list(surf, head_h, foot_h)
        elif self.phase == PHASE_CONNECT:
            self._render_connecting(surf, head_h, foot_h)
        elif self.phase == PHASE_RESULT:
            self._render_result(surf, head_h, foot_h)

        # Hint bar (right side of footer)
        hint = self._hint()
        hint_surf = f_small.render(hint, True, theme.FG_DIM)
        surf.blit(hint_surf, (theme.SCREEN_W - hint_surf.get_width() - theme.PADDING,
                              theme.SCREEN_H - foot_h + 8))

    def _hint(self) -> str:
        if self.phase == PHASE_LIST:
            return "A: Select  X: Rescan  B: Back"
        if self.phase == PHASE_RESULT:
            return ("A: Retry  B: Back" if not self.result_ok else "B: Back")
        return "B: Back"

    def _render_scanning(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f = pygame.font.Font(None, 32)
        msg = f.render("Scanning for networks...", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                        theme.SCREEN_H // 2 - 30))
        # spinner
        cx, cy = theme.SCREEN_W // 2, theme.SCREEN_H // 2 + 20
        ang = (time.time() * 3) % 6.283
        for i in range(8):
            a = ang + i * 0.785
            x = cx + int(math.cos(a) * 18)
            y = cy + int(math.sin(a) * 18)
            shade = 50 + i * 22
            pygame.draw.circle(surf, (shade, shade, shade), (x, y), 3)

    def _render_list(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        if not self.networks:
            f = pygame.font.Font(None, 28)
            msg = f.render("No networks. Press X to rescan.", True, theme.FG_DIM)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2 - 14))
            return

        list_x = theme.PADDING
        list_y = head_h + 8
        list_w = theme.SCREEN_W - 2 * theme.PADDING
        list_h = theme.SCREEN_H - head_h - foot_h - 16
        pygame.draw.rect(surf, (5, 5, 10), (list_x, list_y, list_w, list_h))
        pygame.draw.rect(surf, theme.DIVIDER, (list_x, list_y, list_w, list_h), 1)

        row_h = 44
        f_ssid = pygame.font.Font(None, 26)
        f_meta = pygame.font.Font(None, 20)

        visible = list_h // row_h
        for i in range(visible):
            idx = self.scroll + i
            if idx >= len(self.networks):
                break
            net = self.networks[idx]
            y = list_y + i * row_h
            row_rect = pygame.Rect(list_x + 1, y + 1, list_w - 2, row_h - 2)

            if idx == self.cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, row_rect)
                pygame.draw.rect(surf, theme.ACCENT, row_rect, 2)
                ssid_color = theme.ACCENT
            else:
                ssid_color = theme.FG

            # Lock icon for secured nets
            lock = "*" if not self._is_open(net) else " "
            ssid_surf = f_ssid.render(f"{lock} {net.ssid}", True, ssid_color)
            surf.blit(ssid_surf, (row_rect.x + 10, row_rect.y + 6))

            meta = f"{net.signal:>3}%  {net.security}"
            meta_surf = f_meta.render(meta, True, theme.FG_DIM)
            surf.blit(meta_surf, (row_rect.right - meta_surf.get_width() - 10,
                                  row_rect.y + 12))

        # Scroll indicator
        if len(self.networks) > visible:
            bar_x = list_x + list_w - 4
            bar_h = list_h
            thumb_h = max(20, int(bar_h * visible / len(self.networks)))
            thumb_y = list_y + int(bar_h * self.scroll / len(self.networks))
            pygame.draw.rect(surf, theme.DIVIDER, (bar_x, list_y, 3, bar_h))
            pygame.draw.rect(surf, theme.ACCENT, (bar_x, thumb_y, 3, thumb_h))

    def _render_connecting(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f = pygame.font.Font(None, 30)
        ssid = self.selected.ssid if self.selected else ""
        msg = f.render(f"Connecting to {ssid}...", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                        theme.SCREEN_H // 2 - 30))
        # progress dots
        dots = "." * (1 + int(time.time() * 2) % 4)
        f2 = pygame.font.Font(None, 36)
        d = f2.render(dots, True, theme.ACCENT)
        surf.blit(d, (theme.SCREEN_W // 2 - d.get_width() // 2,
                      theme.SCREEN_H // 2 + 20))

    def _render_result(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f_big = pygame.font.Font(None, 36)
        f_small = pygame.font.Font(None, 22)
        title = "CONNECTED" if self.result_ok else "FAILED"
        color = theme.ACCENT if self.result_ok else theme.ERR

        t = f_big.render(title, True, color)
        surf.blit(t, (theme.SCREEN_W // 2 - t.get_width() // 2, head_h + 30))

        if self.selected:
            s = f_small.render(self.selected.ssid, True, theme.FG)
            surf.blit(s, (theme.SCREEN_W // 2 - s.get_width() // 2, head_h + 80))

        # Word-wrap result text
        body_x = theme.PADDING + 10
        body_y = head_h + 130
        max_w = theme.SCREEN_W - 2 * (theme.PADDING + 10)
        for line in self._wrap(self.result_text, f_small, max_w)[:6]:
            ls = f_small.render(line, True, theme.FG_DIM)
            surf.blit(ls, (body_x, body_y))
            body_y += 24

    @staticmethod
    def _wrap(text: str, font: pygame.font.Font, max_w: int) -> list[str]:
        out: list[str] = []
        for raw in text.splitlines() or [""]:
            words = raw.split(" ")
            cur = ""
            for w in words:
                trial = (cur + " " + w).strip() if cur else w
                if font.size(trial)[0] <= max_w:
                    cur = trial
                else:
                    if cur:
                        out.append(cur)
                    cur = w
            if cur:
                out.append(cur)
        return out
