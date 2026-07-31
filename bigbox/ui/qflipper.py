"""qFlipper — Flipper Zero device manager."""
from __future__ import annotations

import subprocess
import threading
import time
from typing import TYPE_CHECKING

import pygame

from bigbox import theme
from bigbox.events import Button, ButtonEvent

if TYPE_CHECKING:
    from bigbox.app import App


class QFlipperView:
    def __init__(self) -> None:
        self.dismissed = False
        self.device_info = {}
        self.status = "SCANNING"
        self.error_msg = ""
        self._scan_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.connected = False
        self.last_refresh = 0.0

        self.title_font = pygame.font.Font(None, 36)
        self.body_font = pygame.font.Font(None, 24)
        self.small_font = pygame.font.Font(None, 20)

        self._start_scan()

    def _start_scan(self) -> None:
        """Scan for connected Flipper Zero devices."""
        self._stop_event.clear()
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()

    def _scan_loop(self) -> None:
        """Background thread to detect Flipper Zero devices."""
        try:
            # Try to detect Flipper Zero via lsusb (USB connection)
            result = subprocess.run(
                ["lsusb", "-v"],
                capture_output=True,
                text=True,
                timeout=5
            )

            # Flipper Zero USB VID:PID is 0x0483:0x5740 (STM32)
            if "0483:5740" in result.stdout or "Flipper" in result.stdout:
                self.connected = True
                self.status = "CONNECTED"
                self._get_device_info()
            else:
                # Try BLE detection
                self._try_ble_scan()

        except Exception as e:
            self.error_msg = f"Scan failed: {str(e)[:40]}"
            self.status = "ERROR"

    def _try_ble_scan(self) -> None:
        """Try to detect Flipper Zero over BLE."""
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True,
                text=True,
                timeout=5
            )

            # Look for Flipper Zero in BLE device list
            for line in result.stdout.split("\n"):
                if "Flipper" in line:
                    self.connected = True
                    self.status = "CONNECTED (BLE)"
                    # Extract MAC and name
                    parts = line.split()
                    if len(parts) >= 3:
                        mac = parts[1]
                        name = " ".join(parts[2:])
                        self.device_info = {
                            "MAC": mac,
                            "Name": name,
                            "Connection": "Bluetooth LE"
                        }
                    return

            self.status = "NO DEVICE FOUND"
            self.connected = False

        except Exception as e:
            self.error_msg = f"BLE scan failed: {str(e)[:40]}"
            self.status = "ERROR"

    def _get_device_info(self) -> None:
        """Get detailed info from connected Flipper Zero."""
        try:
            # Try to get info via lsusb
            result = subprocess.run(
                ["lsusb", "-v", "-d", "0483:5740"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.stdout:
                lines = result.stdout.split("\n")
                for line in lines:
                    if "iProduct" in line or "iSerialNumber" in line or "iManufacturer" in line:
                        self.device_info[line.split(":")[0].strip()] = line.split(":", 1)[1].strip() if ":" in line else ""

                # Parse basic info
                if "Bus" in result.stdout:
                    self.device_info["Connection"] = "USB"

        except Exception:
            pass

    def handle(self, ev: ButtonEvent, ctx: App) -> None:
        if not ev.pressed:
            return

        if ev.button is Button.B:
            self.dismissed = True
        elif ev.button is Button.A:
            # Try to open qFlipper or launch device control
            self._launch_qflipper()
        elif ev.button is Button.Y:
            # Refresh device scan
            self._start_scan()

    def _launch_qflipper(self) -> None:
        """Launch qFlipper application if available."""
        try:
            subprocess.Popen(["qflipper"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status = "LAUNCHING QFLIPPER..."
        except FileNotFoundError:
            self.error_msg = "qFlipper not installed"
            self.status = "READY"
        except Exception as e:
            self.error_msg = f"Launch failed: {str(e)[:30]}"

    def render(self, surf: pygame.Surface) -> None:
        surf.fill(theme.BG)
        pad = theme.PADDING

        # Title
        title = self.title_font.render("QFLIPPER :: FLIPPER ZERO", True, theme.ACCENT)
        surf.blit(title, (pad, pad))

        # Status pill
        status_color = theme.ACCENT if self.connected else theme.WARN if self.status == "SCANNING" else theme.ERR
        status_surf = self.body_font.render(f"● {self.status}", True, status_color)
        surf.blit(status_surf, (theme.SCREEN_W - pad - status_surf.get_width(), pad + 6))

        y = 70

        # Device info
        if self.device_info:
            info_label = self.body_font.render("DEVICE INFO", True, theme.FG_DIM)
            surf.blit(info_label, (pad, y))
            y += 30

            for key, value in self.device_info.items():
                if value:
                    label = self.small_font.render(f"{key}:", True, theme.FG_DIM)
                    val = self.small_font.render(str(value)[:50], True, theme.FG)
                    surf.blit(label, (pad + 20, y))
                    surf.blit(val, (pad + 180, y))
                    y += 24
        else:
            info_label = self.body_font.render("No device detected", True, theme.FG_DIM)
            surf.blit(info_label, (pad, y))
            y += 40

        # Error message
        if self.error_msg:
            err_surf = self.body_font.render(f"ERROR: {self.error_msg}", True, theme.ERR)
            surf.blit(err_surf, (pad, y))
            y += 30

        # Divider
        y += 10
        pygame.draw.line(surf, theme.DIVIDER, (pad, y), (theme.SCREEN_W - pad, y), 1)

        # Instructions
        instructions = [
            "A: Launch qFlipper",
            "Y: Refresh Scan",
            "B: Back"
        ]
        inst_y = theme.SCREEN_H - 100
        for inst in instructions:
            inst_surf = self.small_font.render(inst, True, theme.FG_DIM)
            surf.blit(inst_surf, (pad, inst_y))
            inst_y += 24
