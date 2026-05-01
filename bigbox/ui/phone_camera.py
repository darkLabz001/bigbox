"""Phone Camera — view a phone's camera stream over the LAN.

Pairs with an Android/iOS app (IP Webcam, DroidCam, etc.) that exposes
the camera as an MJPEG or RTSP URL. The bigbox side just runs the same
ffmpeg → MJPEG-pipe pipeline as CCTVView, but slimmed down for one
user-supplied URL. The URL is persisted at
``/etc/bigbox/phone_camera.json`` so reconnecting is a single tap.

Browser-based capture (phone visits a page on bigbox and streams via
``getUserMedia``) was tempting since it needs no app, but mobile
browsers require HTTPS for camera access on non-localhost hosts —
which means a self-signed cert + warning click each time. The phone-
app route is one-time install for one-tap connect afterwards.
"""
from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


CONFIG_PATH = Path("/etc/bigbox/phone_camera.json")

PHASE_CONFIG = "config"
PHASE_STREAMING = "streaming"


def _load_url() -> str:
    try:
        with CONFIG_PATH.open() as f:
            return (json.load(f).get("url") or "").strip()
    except Exception:
        return ""


def _save_url(url: str) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w") as f:
            json.dump({"url": url}, f)
    except Exception as e:
        print(f"[phone_camera] save failed: {e}")


class PhoneCameraView:
    VIEW_W = 760
    VIEW_H = 380

    def __init__(self) -> None:
        self.dismissed = False
        self.phase = PHASE_CONFIG

        self.url = _load_url()
        self.error_msg: str = ""
        self.fps = 0

        self.title_font = pygame.font.Font(None, theme.FS_TITLE)
        self.body_font = pygame.font.Font(None, theme.FS_BODY)
        self.hint_font = pygame.font.Font(None, theme.FS_SMALL)

        self._proc: subprocess.Popen | None = None
        self._stream_thread: threading.Thread | None = None
        self._stop_thread = False
        self._frame_buffer: deque[pygame.Surface] = deque(maxlen=2)

    # ---------- input ------------------------------------------------------
    def handle(self, ev: ButtonEvent, ctx: "App") -> None:
        if not ev.pressed:
            return

        if self.phase == PHASE_STREAMING:
            if ev.button is Button.B:
                self._stop_stream()
                self.phase = PHASE_CONFIG
            return

        # PHASE_CONFIG
        if ev.button is Button.B:
            self.dismissed = True
            return
        if ev.button is Button.A and self.url:
            self._start_stream()
            return
        if ev.button is Button.X:
            def _on_input(val):
                if val is not None and val.strip():
                    self.url = val.strip()
                    _save_url(self.url)
            ctx.get_input("PHONE CAMERA URL (mjpeg/rtsp/http)",
                          _on_input, self.url)

    # ---------- streaming --------------------------------------------------
    def _start_stream(self) -> None:
        self.error_msg = ""
        self.fps = 0
        self._stop_thread = False
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-rtsp_transport", "tcp",   # silently ignored for non-rtsp inputs
            "-i", self.url,
            "-vf", (f"scale={self.VIEW_W}:{self.VIEW_H}"
                    f":force_original_aspect_ratio=decrease,"
                    f"pad={self.VIEW_W}:{self.VIEW_H}:(ow-iw)/2:(oh-ih)/2"),
            "-r", "15",
            "-q:v", "5",
            "-an",
            "-f", "mjpeg",
            "pipe:1",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            self.error_msg = "ffmpeg not installed"
            return
        except Exception as e:
            self.error_msg = f"ffmpeg: {e}"
            return
        self.phase = PHASE_STREAMING
        self._stream_thread = threading.Thread(target=self._loop, daemon=True)
        self._stream_thread.start()

    def _stop_stream(self) -> None:
        self._stop_thread = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._frame_buffer.clear()

    def _loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        buf = bytearray()
        last_fps_check = time.time()
        frames_this_sec = 0
        while not self._stop_thread:
            chunk = self._proc.stdout.read(32768)
            if not chunk:
                err_b = b""
                if self._proc.stderr:
                    try:
                        err_b = self._proc.stderr.read(512)
                    except Exception:
                        pass
                err = err_b.decode("utf-8", "replace").strip()
                if err:
                    self.error_msg = err.split("\n", 1)[0][:80]
                else:
                    self.error_msg = "Stream ended"
                break
            buf.extend(chunk)
            while True:
                start = buf.find(b"\xff\xd8")
                end = buf.find(b"\xff\xd9", start + 2)
                if start == -1 or end == -1:
                    break
                jpg = bytes(buf[start:end + 2])
                del buf[:end + 2]
                try:
                    frame = pygame.image.load(io.BytesIO(jpg))
                except Exception:
                    continue
                self._frame_buffer.append(frame)
                frames_this_sec += 1
                now = time.time()
                if now - last_fps_check > 1.0:
                    self.fps = frames_this_sec
                    frames_this_sec = 0
                    last_fps_check = now

    # ---------- render -----------------------------------------------------
    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        head_h = 50
        pygame.draw.rect(surf, theme.BG_ALT, (0, 0, theme.SCREEN_W, head_h))
        pygame.draw.line(surf, theme.ACCENT, (0, head_h - 1),
                         (theme.SCREEN_W, head_h - 1), 2)
        title = self.title_font.render("MEDIA :: PHONE_CAMERA",
                                       True, theme.ACCENT)
        surf.blit(title, (theme.PADDING, (head_h - title.get_height()) // 2))

        if self.phase == PHASE_STREAMING:
            self._render_stream(surf, head_h)
        else:
            self._render_config(surf, head_h)

    def _render_stream(self, surf: pygame.Surface, head_h: int) -> None:
        if self._frame_buffer:
            frame = self._frame_buffer[-1]
            x = (theme.SCREEN_W - frame.get_width()) // 2
            y = head_h + 8
            surf.blit(frame, (x, y))
        else:
            msg_text = self.error_msg or "Connecting..."
            color = theme.ERR if self.error_msg else theme.FG
            msg = self.body_font.render(msg_text, True, color)
            surf.blit(msg, (theme.SCREEN_W // 2 - msg.get_width() // 2,
                            theme.SCREEN_H // 2))

        foot = (self.error_msg + "   B: Back" if self.error_msg
                else f"FPS {self.fps}   {self.url[:60]}   B: Back")
        f = self.hint_font.render(foot, True, theme.FG_DIM)
        surf.blit(f, (theme.PADDING, theme.SCREEN_H - 28))

    def _render_config(self, surf: pygame.Surface, head_h: int) -> None:
        y = head_h + 18
        hint_lines = [
            "Install IP Webcam (Android) or DroidCam (iOS) on your phone,",
            "start the server, then paste the URL it shows.",
        ]
        for line in hint_lines:
            ts = self.body_font.render(line, True, theme.FG_DIM)
            surf.blit(ts, (theme.PADDING, y))
            y += 26
        y += 6

        examples = [
            "IP Webcam:   http://<phone-ip>:8080/video",
            "DroidCam:    http://<phone-ip>:4747/mjpegfeed?640x480",
        ]
        for line in examples:
            ts = self.hint_font.render(line, True, theme.FG_DIM)
            surf.blit(ts, (theme.PADDING + 12, y))
            y += 22
        y += 14

        url_label = self.body_font.render("URL:", True, theme.ACCENT)
        surf.blit(url_label, (theme.PADDING, y))
        url_color = theme.FG if self.url else theme.FG_DIM
        url_text = self.url or "(not set — press X)"
        url_surf = self.body_font.render(url_text, True, url_color)
        surf.blit(url_surf, (theme.PADDING + 60, y))
        y += 36

        if self.error_msg:
            err = self.body_font.render(self.error_msg, True, theme.ERR)
            surf.blit(err, (theme.PADDING, y))

        if self.url:
            hint_text = "A: Connect    X: Edit URL    B: Back"
        else:
            hint_text = "X: Set URL    B: Back"
        f = self.hint_font.render(hint_text, True, theme.FG_DIM)
        surf.blit(f, (theme.PADDING, theme.SCREEN_H - 28))
