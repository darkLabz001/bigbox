"""IP Camera Scanner — find cameras on the local network and view them.

Distinct from `cctv.py` (which targets a curated list of public Internet
cameras). This view discovers hosts on the LAN with common IP-camera ports
open, lets the user pick one, and tries a series of well-known snapshot /
MJPEG URLs to display a feed.
"""
from __future__ import annotations

import io
import ipaddress
import re
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import pygame
import requests

from bigbox import theme
from bigbox.events import Button, ButtonEvent
from bigbox.ui.section import SectionContext


# Ports commonly hosting camera HTTP / RTSP services.
CAMERA_PORTS = [80, 81, 88, 554, 8000, 8080, 8081, 8443, 8554, 8888, 37777, 34567]

# HTTP paths to probe for a single still image.
SNAPSHOT_PATHS = [
    "/snapshot.jpg",
    "/snap.jpg",
    "/image.jpg",
    "/cgi-bin/snapshot.cgi",
    "/cgi-bin/snapshot.cgi?chn=0",
    "/axis-cgi/jpg/image.cgi",
    "/jpg/image.jpg",
    "/onvif-http/snapshot",
    "/cgi-bin/CGIProxy.fcgi?cmd=snapPicture2",
    "/Streaming/channels/1/picture",  # Hikvision
    "/ISAPI/Streaming/channels/101/picture",
]

# HTTP paths to probe for a multipart MJPEG stream.
MJPEG_PATHS = [
    "/video.cgi",
    "/mjpg/video.mjpg",
    "/axis-cgi/mjpg/video.cgi",
    "/cgi-bin/mjpg/video.cgi",
    "/videostream.cgi",
    "/nphMotionJpeg",
    "/mjpegfeed",
    "/video.mjpg",
    "/cam_1.cgi",
]

PHASE_PICK_IFACE = "iface"
PHASE_SCAN = "scan"
PHASE_LIST = "list"
PHASE_VIEW = "view"


@dataclass
class CamCandidate:
    ip: str
    port: int
    banner: str = ""  # Server / WWW-Authenticate / title hint
    working_url: str | None = None
    is_mjpeg: bool = False


@dataclass
class _Iface:
    name: str
    cidr: str  # e.g. "192.168.1.0/24"


def _list_local_subnets() -> list[_Iface]:
    """Use `ip -o -4 addr` to enumerate IPv4 interfaces with their /N."""
    try:
        out = subprocess.check_output(["ip", "-o", "-4", "addr"], text=True)
    except Exception:
        return []
    res: list[_Iface] = []
    for line in out.splitlines():
        # Format: "3: wlan0    inet 192.168.1.42/24 brd ... scope global ..."
        m = re.search(r"\d+:\s+(\S+)\s+inet\s+([\d.]+/\d+)", line)
        if not m:
            continue
        name, cidr = m.group(1), m.group(2)
        if name == "lo":
            continue
        try:
            net = ipaddress.IPv4Network(cidr, strict=False)
            # Skip link-local / oversized scans.
            if net.prefixlen < 22:
                continue
            res.append(_Iface(name=name, cidr=str(net)))
        except ValueError:
            continue
    return res


class CamScannerView:
    """Discover and view IP cameras on the local network."""

    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_PICK_IFACE
        self.status_msg = "Select interface"

        self.ifaces = _list_local_subnets()
        self.iface_cursor = 0

        self.candidates: list[CamCandidate] = []
        self.list_cursor = 0
        self.list_scroll = 0

        # Streaming state (PHASE_VIEW)
        self.view_w = 540
        self.view_h = 380
        self._frame_buffer: deque = deque(maxlen=1)
        self._stream_thread: threading.Thread | None = None
        self._stop_stream = False
        self.is_loading = False
        self.error_msg: str | None = None
        self.fps = 0.0
        self.zoom = 1
        self.viewing: CamCandidate | None = None

        # Scan state
        self._scan_thread: threading.Thread | None = None
        self._stop_scan = False
        self._scan_proc: subprocess.Popen | None = None

        if not self.ifaces:
            self.status_msg = "No usable interface"

    # ---------- scan ----------
    def _start_scan(self) -> None:
        if not self.ifaces:
            return
        iface = self.ifaces[self.iface_cursor]
        self.phase = PHASE_SCAN
        self.candidates = []
        self.list_cursor = 0
        self.list_scroll = 0
        self.status_msg = f"Scanning {iface.cidr}..."
        self._stop_scan = False
        self._scan_thread = threading.Thread(
            target=self._scan_worker, args=(iface,), daemon=True
        )
        self._scan_thread.start()

    def _scan_worker(self, iface: _Iface) -> None:
        ports_arg = ",".join(str(p) for p in CAMERA_PORTS)
        cmd = [
            "nmap", "-n", "-Pn", "-T4", "--open",
            "-p", ports_arg,
            "-oG", "-",
            iface.cidr,
        ]
        try:
            self._scan_proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
        except FileNotFoundError:
            self.status_msg = "nmap not installed"
            self.phase = PHASE_LIST
            return

        try:
            assert self._scan_proc.stdout is not None
            for line in self._scan_proc.stdout:
                if self._stop_scan:
                    break
                # Greppable: "Host: 192.168.1.5 ()  Ports: 80/open/tcp//http/.../, 554/open/..."
                if "Ports:" not in line or "open" not in line:
                    continue
                m_host = re.search(r"Host:\s+([\d.]+)", line)
                if not m_host:
                    continue
                ip = m_host.group(1)
                ports_part = line.split("Ports:", 1)[1]
                for port_block in ports_part.split(","):
                    pm = re.match(r"\s*(\d+)/open/", port_block)
                    if not pm:
                        continue
                    port = int(pm.group(1))
                    cand = CamCandidate(ip=ip, port=port)
                    self.candidates.append(cand)
                    # Probe banner in a side thread so list keeps populating.
                    threading.Thread(
                        target=self._probe_banner, args=(cand,), daemon=True
                    ).start()

            self._scan_proc.wait(timeout=5)
        except Exception as e:
            self.status_msg = f"scan error: {type(e).__name__}"
        finally:
            if not self._stop_scan:
                self.status_msg = (
                    f"{len(self.candidates)} candidates"
                    if self.candidates else "No cameras found"
                )
            self.phase = PHASE_LIST
            self._scan_proc = None

    def _probe_banner(self, cand: CamCandidate) -> None:
        """Cheap HTTP HEAD/GET to grab Server / WWW-Authenticate / <title>."""
        if cand.port == 554:
            cand.banner = "RTSP"
            return
        scheme = "https" if cand.port in (443, 8443) else "http"
        url = f"{scheme}://{cand.ip}:{cand.port}/"
        try:
            r = requests.get(url, timeout=3, allow_redirects=False, verify=False)
        except Exception:
            return
        bits: list[str] = []
        srv = r.headers.get("Server")
        if srv:
            bits.append(srv[:30])
        auth = r.headers.get("WWW-Authenticate", "")
        m = re.search(r'realm="?([^"\s]+)', auth)
        if m:
            bits.append(f"realm:{m.group(1)[:20]}")
        if "text/html" in r.headers.get("Content-Type", "").lower():
            tm = re.search(r"<title>([^<]{1,40})</title>", r.text, re.I)
            if tm:
                bits.append(tm.group(1).strip())
        cand.banner = " | ".join(bits)[:60]

    # ---------- view ----------
    def _start_view(self, cand: CamCandidate) -> None:
        self.viewing = cand
        self.phase = PHASE_VIEW
        self._frame_buffer.clear()
        self.is_loading = True
        self.error_msg = None
        self.fps = 0.0
        self.zoom = 1
        self.status_msg = f"Probing {cand.ip}:{cand.port}..."
        self._stop_stream = False
        self._stream_thread = threading.Thread(
            target=self._stream_worker, args=(cand,), daemon=True
        )
        self._stream_thread.start()

    def _stop_view(self) -> None:
        self._stop_stream = True
        if self._stream_thread:
            self._stream_thread.join(timeout=1.0)
        self._stream_thread = None
        self.viewing = None
        self.phase = PHASE_LIST
        self.status_msg = (
            f"{len(self.candidates)} candidates"
            if self.candidates else "No cameras found"
        )

    def _stream_worker(self, cand: CamCandidate) -> None:
        if cand.port == 554:
            self.error_msg = f"RTSP at rtsp://{cand.ip}:554/  (use VLC)"
            self.is_loading = False
            return

        scheme = "https" if cand.port in (443, 8443) else "http"
        base = f"{scheme}://{cand.ip}:{cand.port}"

        # 1. Try MJPEG paths.
        for path in MJPEG_PATHS:
            if self._stop_stream:
                return
            url = base + path
            try:
                r = requests.get(url, timeout=3, stream=True,
                                 allow_redirects=True, verify=False)
            except Exception:
                continue
            ct = r.headers.get("Content-Type", "").lower()
            if r.status_code == 200 and "multipart" in ct:
                cand.working_url = url
                cand.is_mjpeg = True
                self.is_loading = False
                self.status_msg = f"MJPEG {url}"
                self._mjpeg_loop(r)
                return
            r.close()

        # 2. Try snapshot paths.
        for path in SNAPSHOT_PATHS:
            if self._stop_stream:
                return
            url = base + path
            try:
                r = requests.get(url, timeout=3, allow_redirects=True, verify=False)
            except Exception:
                continue
            ct = r.headers.get("Content-Type", "").lower()
            if r.status_code == 200 and ("image" in ct or url.endswith((".jpg", ".jpeg"))):
                cand.working_url = url
                cand.is_mjpeg = False
                self.is_loading = False
                self.status_msg = f"snapshot {url}"
                self._snapshot_loop(url)
                return
            r.close()

        # 3. Nothing worked.
        self.is_loading = False
        self.error_msg = "No known stream URL responded"

    def _mjpeg_loop(self, resp: requests.Response) -> None:
        buf = bytearray()
        last_check = time.time()
        frames = 0
        try:
            for chunk in resp.iter_content(chunk_size=32768):
                if self._stop_stream:
                    break
                buf.extend(chunk)
                while True:
                    a = buf.find(b"\xff\xd8")
                    b = buf.find(b"\xff\xd9", a + 2)
                    if a == -1 or b == -1:
                        break
                    jpg = bytes(buf[a:b + 2])
                    del buf[:b + 2]
                    try:
                        surf = pygame.image.load(io.BytesIO(jpg))
                        surf = self._apply_zoom_scale(surf)
                        self._frame_buffer.append(surf)
                        frames += 1
                        now = time.time()
                        if now - last_check > 1.0:
                            self.fps = frames
                            frames = 0
                            last_check = now
                    except Exception:
                        pass
                if len(buf) > 1024 * 1024:
                    buf = bytearray()
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _snapshot_loop(self, url: str) -> None:
        while not self._stop_stream:
            try:
                r = requests.get(url, timeout=4, verify=False)
                if r.status_code == 200:
                    surf = pygame.image.load(io.BytesIO(r.content))
                    surf = self._apply_zoom_scale(surf)
                    self._frame_buffer.append(surf)
                    self.fps = 1.0
                else:
                    self.error_msg = f"HTTP {r.status_code}"
            except Exception as e:
                self.error_msg = f"{type(e).__name__}"
            # Poll every ~1s
            t = time.time()
            while time.time() - t < 1.0 and not self._stop_stream:
                time.sleep(0.05)

    def _apply_zoom_scale(self, surf: pygame.Surface) -> pygame.Surface:
        if self.zoom > 1:
            w, h = surf.get_size()
            cw, ch = w // self.zoom, h // self.zoom
            cx, cy = (w - cw) // 2, (h - ch) // 2
            surf = surf.subsurface((cx, cy, cw, ch))
        return pygame.transform.scale(surf, (self.view_w, self.view_h))

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
                self._start_scan()
            return

        if self.phase == PHASE_SCAN:
            if ev.button is Button.B:
                self._stop_scan = True
                if self._scan_proc:
                    try:
                        self._scan_proc.kill()
                    except Exception:
                        pass
                self.status_msg = "Scan canceled"
                self.phase = PHASE_LIST
            return

        if self.phase == PHASE_LIST:
            if ev.button is Button.B:
                self.phase = PHASE_PICK_IFACE
                self.status_msg = "Select interface"
            elif ev.button is Button.X:
                self._start_scan()
            elif not self.candidates:
                return
            elif ev.button is Button.UP:
                self.list_cursor = (self.list_cursor - 1) % len(self.candidates)
                self._adjust_scroll()
            elif ev.button is Button.DOWN:
                self.list_cursor = (self.list_cursor + 1) % len(self.candidates)
                self._adjust_scroll()
            elif ev.button is Button.A:
                self._start_view(self.candidates[self.list_cursor])
            return

        if self.phase == PHASE_VIEW:
            if ev.button is Button.B:
                self._stop_view()
            elif ev.button is Button.UP and not ev.repeat:
                self.zoom = 2 if self.zoom == 1 else (4 if self.zoom == 2 else 1)
            return

    def _adjust_scroll(self) -> None:
        visible = 8
        if self.list_cursor < self.list_scroll:
            self.list_scroll = self.list_cursor
        elif self.list_cursor >= self.list_scroll + visible:
            self.list_scroll = self.list_cursor - visible + 1

    # ---------- render ----------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)

        head_h = 44
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        f_title = pygame.font.Font(None, 32)
        surf.blit(f_title.render("RECON :: CAM_SCAN", True, theme.ACCENT),
                  (theme.PADDING, 8))

        foot_h = 32
        pygame.draw.rect(surf, (10, 10, 20),
                         (0, theme.SCREEN_H - foot_h, theme.SCREEN_W, foot_h))
        pygame.draw.line(surf, theme.DIVIDER,
                         (0, theme.SCREEN_H - foot_h),
                         (theme.SCREEN_W, theme.SCREEN_H - foot_h))
        f_small = pygame.font.Font(None, 20)
        surf.blit(f_small.render(self.status_msg[:60], True, theme.ACCENT),
                  (theme.PADDING, theme.SCREEN_H - foot_h + 8))
        hint = self._hint()
        hint_surf = f_small.render(hint, True, theme.FG_DIM)
        surf.blit(hint_surf,
                  (theme.SCREEN_W - hint_surf.get_width() - theme.PADDING,
                   theme.SCREEN_H - foot_h + 8))

        if self.phase == PHASE_PICK_IFACE:
            self._render_iface(surf, head_h, foot_h)
        elif self.phase == PHASE_SCAN:
            self._render_scanning(surf, head_h, foot_h)
        elif self.phase == PHASE_LIST:
            self._render_list(surf, head_h, foot_h)
        elif self.phase == PHASE_VIEW:
            self._render_view(surf, head_h, foot_h)

    def _hint(self) -> str:
        if self.phase == PHASE_PICK_IFACE:
            return "A: Scan  B: Back"
        if self.phase == PHASE_SCAN:
            return "B: Cancel"
        if self.phase == PHASE_LIST:
            return "A: View  X: Rescan  B: Back"
        if self.phase == PHASE_VIEW:
            return "UP: Zoom  B: Back"
        return ""

    def _render_iface(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f = pygame.font.Font(None, 32)
        f_small = pygame.font.Font(None, 22)
        title = f.render("Pick interface to scan", True, theme.FG)
        surf.blit(title, (theme.SCREEN_W // 2 - title.get_width() // 2, head_h + 30))

        if not self.ifaces:
            msg = f_small.render("No usable IPv4 interface found.", True, theme.ERR)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2))
            return

        list_y = head_h + 90
        for i, iface in enumerate(self.ifaces):
            sel = i == self.iface_cursor
            rect = pygame.Rect(theme.SCREEN_W // 2 - 220, list_y + i * 50, 440, 44)
            if sel:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect, border_radius=5)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2, border_radius=5)
            color = theme.ACCENT if sel else theme.FG
            label = f.render(f"{iface.name}", True, color)
            sub = f_small.render(iface.cidr, True, theme.FG_DIM)
            surf.blit(label, (rect.x + 14, rect.y + 8))
            surf.blit(sub, (rect.right - sub.get_width() - 14, rect.y + 14))

    def _render_scanning(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        f = pygame.font.Font(None, 30)
        msg = f.render("Scanning subnet for cameras...", True, theme.FG)
        surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                        theme.SCREEN_H // 2 - 40))
        f_small = pygame.font.Font(None, 22)
        cnt = f_small.render(f"Found so far: {len(self.candidates)}",
                             True, theme.ACCENT)
        surf.blit(cnt, (theme.SCREEN_W // 2 - cnt.get_width() // 2,
                        theme.SCREEN_H // 2 + 10))
        # marquee bar
        bar_y = theme.SCREEN_H // 2 + 60
        bar_w = 360
        bar_x = theme.SCREEN_W // 2 - bar_w // 2
        pygame.draw.rect(surf, theme.DIVIDER, (bar_x, bar_y, bar_w, 6))
        pos = (time.time() * 220) % (bar_w + 80) - 80
        pygame.draw.rect(surf, theme.ACCENT,
                         (bar_x + max(0, int(pos)), bar_y, 80, 6))

    def _render_list(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        if not self.candidates:
            f = pygame.font.Font(None, 26)
            msg = f.render("No camera ports open. X to rescan, B to back.",
                           True, theme.FG_DIM)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2))
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
            idx = self.list_scroll + i
            if idx >= len(self.candidates):
                break
            c = self.candidates[idx]
            y = list_y + i * row_h
            rect = pygame.Rect(list_x + 1, y + 1, list_w - 2, row_h - 2)
            if idx == self.list_cursor:
                pygame.draw.rect(surf, theme.SELECTION_BG, rect)
                pygame.draw.rect(surf, theme.ACCENT, rect, 2)
                color = theme.ACCENT
            else:
                color = theme.FG

            label = f_main.render(f"{c.ip}:{c.port}", True, color)
            surf.blit(label, (rect.x + 10, rect.y + 4))

            banner = c.banner or "..."
            bsurf = f_meta.render(banner, True, theme.FG_DIM)
            surf.blit(bsurf, (rect.x + 10, rect.y + 24))

        # Scroll thumb
        if len(self.candidates) > visible:
            bar_x = list_x + list_w - 4
            thumb_h = max(20, int(list_h * visible / len(self.candidates)))
            thumb_y = list_y + int(list_h * self.list_scroll / len(self.candidates))
            pygame.draw.rect(surf, theme.DIVIDER, (bar_x, list_y, 3, list_h))
            pygame.draw.rect(surf, theme.ACCENT, (bar_x, thumb_y, 3, thumb_h))

    def _render_view(self, surf: pygame.Surface, head_h: int, foot_h: int) -> None:
        cand = self.viewing
        if not cand:
            return

        view_x = (theme.SCREEN_W - self.view_w) // 2
        view_y = head_h + 16
        view = pygame.Rect(view_x, view_y, self.view_w, self.view_h)
        pygame.draw.rect(surf, (0, 0, 0), view)
        pygame.draw.rect(surf, theme.ACCENT_DIM, view, 2)

        if self._frame_buffer:
            surf.blit(self._frame_buffer[0], view.topleft)

        f_small = pygame.font.Font(None, 22)
        target = f_small.render(f"{cand.ip}:{cand.port}", True, theme.ACCENT)
        surf.blit(target, (view.x + 8, view.y + 6))
        if cand.working_url:
            url_surf = f_small.render(cand.working_url[:50], True, theme.FG_DIM)
            surf.blit(url_surf, (view.x + 8, view.bottom - 24))
        sig = f_small.render(f"{self.fps:.1f} FPS  ZOOM {self.zoom}x",
                             True, theme.FG)
        surf.blit(sig, (view.right - sig.get_width() - 8, view.y + 6))

        if self.is_loading:
            msg = f_small.render("PROBING URLS...", True, theme.ACCENT)
            surf.blit(msg, (view.centerx - msg.get_width() // 2, view.centery - 10))
        elif self.error_msg and not self._frame_buffer:
            err_lines = [self.error_msg[i:i+50]
                         for i in range(0, len(self.error_msg), 50)][:3]
            for i, line in enumerate(err_lines):
                msg = f_small.render(line, True, theme.ERR)
                surf.blit(msg, (view.centerx - msg.get_width() // 2,
                                view.centery - 10 + i * 22))
