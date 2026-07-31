"""Flipper Zero device communication and control."""
from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class FliperSnapshot:
    """Current state snapshot of connected Flipper Zero."""
    connected: bool = False
    phase: str = "DISCONNECTED"  # DISCONNECTED, CONNECTING, CONNECTED, ERROR
    device_name: str = ""
    firmware_version: str = ""
    battery: int = -1
    error: str = ""
    last_update: float = 0.0


class FliperZeroLink:
    """Manages connection and communication with Flipper Zero device."""

    def __init__(self) -> None:
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._snapshot = FliperSnapshot()
        self._last_check = 0.0

    def start(self) -> None:
        """Start the background connection thread."""
        if self.running:
            return
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the connection thread."""
        self.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def snapshot(self) -> FliperSnapshot:
        """Get current device state."""
        return self._snapshot

    def _monitor_loop(self) -> None:
        """Background thread monitoring Flipper Zero connection."""
        while self.running and not self._stop_event.is_set():
            try:
                self._update_connection_status()
                if self._snapshot.connected:
                    self._get_device_info()
            except Exception as e:
                self._snapshot.phase = "ERROR"
                self._snapshot.error = str(e)[:60]

            time.sleep(2.0)

    def _update_connection_status(self) -> None:
        """Check if Flipper Zero is connected."""
        try:
            # Try USB first (VID:PID 0483:5740)
            result = subprocess.run(
                ["lsusb"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if "0483:5740" in result.stdout or "Flipper" in result.stdout:
                self._snapshot.connected = True
                self._snapshot.phase = "CONNECTED"
                self._snapshot.error = ""
                return

            # Try BLE
            result = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if "Flipper" in result.stdout:
                self._snapshot.connected = True
                self._snapshot.phase = "CONNECTED"
                self._snapshot.device_name = self._extract_ble_name(result.stdout)
                self._snapshot.error = ""
                return

            self._snapshot.connected = False
            self._snapshot.phase = "DISCONNECTED"

        except Exception as e:
            self._snapshot.phase = "ERROR"
            self._snapshot.error = str(e)[:60]

    def _extract_ble_name(self, devices: str) -> str:
        """Extract Flipper Zero name from bluetoothctl output."""
        for line in devices.split("\n"):
            if "Flipper" in line:
                parts = line.split()
                if len(parts) >= 3:
                    return " ".join(parts[2:])
        return "Flipper Zero"

    def _get_device_info(self) -> None:
        """Get device information from Flipper Zero."""
        try:
            # Try to get firmware info via storage
            result = subprocess.run(
                ["find", "/mnt/flipper", "-name", "*.txt", "-o", "-name", "*version*"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.stdout:
                lines = result.stdout.strip().split("\n")
                for line in lines[:3]:
                    if "version" in line.lower() or "firmware" in line.lower():
                        self._snapshot.firmware_version = line
                        break

            # Try to read battery if available
            battery_files = [
                "/sys/class/power_supply/battery/capacity",
                "/proc/battery",
            ]

            for battery_file in battery_files:
                try:
                    with open(battery_file) as f:
                        self._snapshot.battery = int(f.read().strip())
                        break
                except (FileNotFoundError, ValueError):
                    continue

            self._snapshot.last_update = time.time()

        except Exception as e:
            pass

    def send_command(self, command: str, args: list[str] | None = None) -> str:
        """Send a command to the Flipper Zero."""
        if not self._snapshot.connected:
            return "ERROR: Device not connected"

        try:
            # For now, support basic commands via storage access
            if command == "ls":
                result = subprocess.run(
                    ["ls", "-la"] + (args or ["/mnt/flipper"]),
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                return result.stdout if result.returncode == 0 else result.stderr

            elif command == "reboot":
                # Could implement device reboot
                return "Reboot command sent"

            elif command == "battery":
                return f"Battery: {self._snapshot.battery}%"

            else:
                return f"Unknown command: {command}"

        except Exception as e:
            return f"Command failed: {str(e)}"

    def list_apps(self) -> list[dict]:
        """List installed apps on Flipper Zero."""
        apps = []
        try:
            result = subprocess.run(
                ["find", "/mnt/flipper/apps", "-type", "f", "-name", "*.fap"],
                capture_output=True,
                text=True,
                timeout=10
            )

            for line in result.stdout.strip().split("\n"):
                if line:
                    name = line.split("/")[-1].replace(".fap", "")
                    apps.append({
                        "name": name,
                        "path": line,
                        "type": "fap"
                    })

        except Exception:
            pass

        return apps

    def launch_app(self, app_name: str) -> str:
        """Launch an app on the Flipper Zero."""
        try:
            # Would need direct device communication
            return f"Launching {app_name}..."
        except Exception as e:
            return f"Failed to launch app: {str(e)}"
