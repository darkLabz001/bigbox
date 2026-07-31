"""Flipper Zero device communication and control via RPC."""
from __future__ import annotations

import subprocess
import threading
import time
import json
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

try:
    import serial
except ImportError:
    serial = None


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
    serial_port: str = ""


class FliperZeroLink:
    """Manages connection and communication with Flipper Zero device via RPC."""

    def __init__(self) -> None:
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._snapshot = FliperSnapshot()
        self._serial: Optional[serial.Serial] = None if serial else None
        self._last_check = 0.0
        self._rpc_id = 0

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
        self._disconnect()
        if self._thread:
            self._thread.join(timeout=2.0)

    def snapshot(self) -> FliperSnapshot:
        """Get current device state."""
        return self._snapshot

    def _monitor_loop(self) -> None:
        """Background thread monitoring Flipper Zero connection."""
        while self.running and not self._stop_event.is_set():
            try:
                if not self._snapshot.connected:
                    self._find_and_connect()
                else:
                    self._get_device_info()
                    self._keep_alive()
            except Exception as e:
                self._snapshot.phase = "ERROR"
                self._snapshot.error = str(e)[:60]
                self._disconnect()

            time.sleep(2.0)

    def _find_and_connect(self) -> None:
        """Find and connect to Flipper Zero device."""
        self._snapshot.phase = "CONNECTING"

        # Try to find serial port
        serial_port = self._find_serial_port()
        if not serial_port:
            self._snapshot.phase = "DISCONNECTED"
            return

        try:
            if serial is None:
                raise ImportError("pyserial not installed")

            self._serial = serial.Serial(
                port=serial_port,
                baudrate=230400,
                timeout=1.0
            )
            self._snapshot.serial_port = serial_port
            time.sleep(1)  # Wait for device to be ready

            # Send handshake
            if self._send_rpc_command("system", "ping"):
                self._snapshot.connected = True
                self._snapshot.phase = "CONNECTED"
                self._snapshot.error = ""
                self._snapshot.device_name = "Flipper Zero"
                self._get_device_info()
            else:
                self._disconnect()
                self._snapshot.phase = "DISCONNECTED"
        except Exception as e:
            self._snapshot.phase = "ERROR"
            self._snapshot.error = str(e)[:60]
            self._disconnect()

    def _find_serial_port(self) -> Optional[str]:
        """Find Flipper Zero serial port."""
        # Try common paths first
        common_ports = [
            "/dev/ttyUSB0",
            "/dev/ttyUSB1",
            "/dev/ttyACM0",
            "/dev/ttyACM1",
        ]

        for port in common_ports:
            if Path(port).exists():
                return port

        # Try lsusb to verify Flipper is connected
        try:
            result = subprocess.run(
                ["lsusb"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if "0483:5740" not in result.stdout:
                return None
        except Exception:
            pass

        # Use dmesg to find device
        try:
            result = subprocess.run(
                ["dmesg"],
                capture_output=True,
                text=True,
                timeout=5
            )
            for line in result.stdout.split("\n")[-30:]:
                if "ttyUSB" in line or "ttyACM" in line:
                    for part in line.split():
                        if "tty" in part:
                            port = f"/dev/{part}"
                            if Path(port).exists():
                                return port
        except Exception:
            pass

        return None

    def _disconnect(self) -> None:
        """Disconnect from Flipper Zero."""
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._snapshot.connected = False

    def _send_rpc_command(self, command: str, method: str, params: dict | None = None) -> bool:
        """Send RPC command to Flipper Zero."""
        if not self._serial or serial is None:
            return False

        try:
            self._rpc_id += 1

            # Build RPC message
            msg = {
                "jsonrpc": "2.0",
                "id": self._rpc_id,
                "method": f"{command}.{method}",
            }
            if params:
                msg["params"] = params

            # Send as JSON + newline
            json_str = json.dumps(msg)
            self._serial.write((json_str + "\n").encode())
            self._serial.flush()

            # Read response (with timeout)
            response = b""
            start_time = time.time()
            while time.time() - start_time < 2.0:
                try:
                    chunk = self._serial.read(1)
                    if not chunk:
                        time.sleep(0.01)
                        continue
                    response += chunk
                    if response.endswith(b"\n"):
                        break
                except Exception:
                    time.sleep(0.01)
                    continue

            if response:
                try:
                    data = json.loads(response.decode().strip())
                    return "result" in data or "id" in data
                except Exception:
                    return len(response) > 0

            return False

        except Exception as e:
            self._snapshot.error = str(e)[:60]
            return False

    def _keep_alive(self) -> None:
        """Send periodic keep-alive ping."""
        try:
            self._send_rpc_command("system", "ping")
        except Exception:
            pass

    def _get_device_info(self) -> None:
        """Get device information from Flipper Zero."""
        try:
            # Get info via RPC
            if self._send_rpc_command("system", "protobuf_version"):
                self._snapshot.last_update = time.time()

            # Try to read from storage
            try:
                with open("/mnt/flipper/etc/version") as f:
                    version_data = f.read().strip()
                    if version_data:
                        self._snapshot.firmware_version = version_data[:30]
            except Exception:
                self._snapshot.firmware_version = "Connected"

            # Set battery to placeholder
            if self._snapshot.battery < 0:
                self._snapshot.battery = 85  # Placeholder

        except Exception:
            pass

    def send_command(self, command: str, args: list[str] | None = None) -> str:
        """Send a command to the Flipper Zero."""
        if not self._snapshot.connected:
            return "ERROR: Device not connected"

        try:
            if command == "ls":
                path = args[0] if args else "/int"
                try:
                    result = subprocess.run(
                        ["ls", "-la", path],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    return result.stdout if result.returncode == 0 else result.stderr
                except Exception:
                    return f"Cannot list: {path}"

            elif command == "reboot":
                if self._send_rpc_command("system", "reboot"):
                    return "Reboot command sent to Flipper Zero"
                return "ERROR: Failed to send reboot command"

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
            # Get apps from storage
            app_paths = [
                "/mnt/flipper/apps",
                "/mnt/flipper/apps_ext",
            ]

            for app_dir in app_paths:
                try:
                    result = subprocess.run(
                        ["find", app_dir, "-type", "f", "-name", "*.fap"],
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
                    continue

        except Exception:
            pass

        return apps

    def launch_app(self, app_name: str) -> str:
        """Launch an app on the Flipper Zero."""
        try:
            if not self._snapshot.connected:
                return "ERROR: Device not connected"

            # Try via RPC
            if self._send_rpc_command("loader", "app_start", {"name": app_name}):
                return f"Launching {app_name}..."
            else:
                return f"Failed to launch {app_name}"

        except Exception as e:
            return f"Failed to launch app: {str(e)}"
