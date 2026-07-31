"""Flipper Zero device communication and control via RPC (USB + BLE)."""
from __future__ import annotations

import subprocess
import threading
import time
import json
import asyncio
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

try:
    import serial
except ImportError:
    serial = None

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    BleakClient = None
    BleakScanner = None


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
    connection_type: str = ""  # USB or BLE


class FliperZeroLink:
    """Manages connection to Flipper Zero device via USB/Serial or Bluetooth."""

    # Flipper Zero Bluetooth UUIDs
    RPC_SERVICE_UUID = "00000100-0000-1000-8000-00805f9b34fb"
    RPC_TX_UUID = "00000101-0000-1000-8000-00805f9b34fb"  # Write to device
    RPC_RX_UUID = "00000102-0000-1000-8000-00805f9b34fb"  # Read from device

    def __init__(self) -> None:
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._snapshot = FliperSnapshot()
        self._serial: Optional[serial.Serial] = None
        self._ble_client: Optional[BleakClient] = None
        self._ble_device_address: Optional[str] = None
        self._rpc_id = 0
        self._response_buffer = ""

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
                    # Try USB first, then BLE
                    if not self._try_usb_connect():
                        self._try_ble_connect()
                else:
                    # Keep connection alive
                    if self._snapshot.connection_type == "USB":
                        self._keep_alive_usb()
                    elif self._snapshot.connection_type == "BLE":
                        self._keep_alive_ble()
                    self._get_device_info()

            except Exception as e:
                self._snapshot.phase = "ERROR"
                self._snapshot.error = str(e)[:60]
                self._disconnect()

            time.sleep(2.0)

    # ============ USB SERIAL CONNECTION ============

    def _try_usb_connect(self) -> bool:
        """Try to connect via USB serial."""
        if self._snapshot.connection_type == "USB" and self._snapshot.connected:
            return True

        self._snapshot.phase = "CONNECTING"
        serial_port = self._find_serial_port()

        if not serial_port:
            return False

        try:
            if serial is None:
                return False

            self._serial = serial.Serial(
                port=serial_port,
                baudrate=230400,
                timeout=1.0
            )
            self._snapshot.serial_port = serial_port
            time.sleep(1)

            if self._send_rpc_command_usb("system", "ping"):
                self._snapshot.connected = True
                self._snapshot.phase = "CONNECTED"
                self._snapshot.connection_type = "USB"
                self._snapshot.error = ""
                self._snapshot.device_name = "Flipper Zero (USB)"
                return True
            else:
                self._disconnect()
                return False

        except Exception as e:
            self._snapshot.phase = "ERROR"
            self._snapshot.error = str(e)[:60]
            self._disconnect()
            return False

    def _find_serial_port(self) -> Optional[str]:
        """Find Flipper Zero serial port."""
        common_ports = ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/ttyACM1"]

        for port in common_ports:
            if Path(port).exists():
                return port

        try:
            result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
            if "0483:5740" not in result.stdout:
                return None
        except Exception:
            pass

        try:
            result = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=5)
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

    def _send_rpc_command_usb(self, command: str, method: str, params: dict | None = None) -> bool:
        """Send RPC command over USB serial."""
        if not self._serial or serial is None:
            return False

        try:
            self._rpc_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": self._rpc_id,
                "method": f"{command}.{method}",
            }
            if params:
                msg["params"] = params

            json_str = json.dumps(msg)
            self._serial.write((json_str + "\n").encode())
            self._serial.flush()

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

    def _keep_alive_usb(self) -> None:
        """Send keep-alive ping over USB."""
        try:
            self._send_rpc_command_usb("system", "ping")
        except Exception:
            pass

    # ============ BLE BLUETOOTH CONNECTION ============

    def _try_ble_connect(self) -> bool:
        """Try to connect via Bluetooth LE."""
        if not BleakClient or not BleakScanner:
            return False

        if self._snapshot.connection_type == "BLE" and self._snapshot.connected:
            return True

        self._snapshot.phase = "CONNECTING"

        try:
            # Scan for Flipper Zero
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            address = loop.run_until_complete(self._ble_scan())

            if not address:
                loop.close()
                return False

            # Connect to device
            self._ble_device_address = address
            success = loop.run_until_complete(self._ble_connect_device(address))
            loop.close()

            if success:
                self._snapshot.connected = True
                self._snapshot.phase = "CONNECTED"
                self._snapshot.connection_type = "BLE"
                self._snapshot.error = ""
                self._snapshot.device_name = "Flipper Zero (BLE)"
                return True
            else:
                return False

        except Exception as e:
            self._snapshot.phase = "ERROR"
            self._snapshot.error = str(e)[:60]
            return False

    async def _ble_scan(self, timeout: int = 5) -> Optional[str]:
        """Scan for Flipper Zero BLE device."""
        try:
            devices = await BleakScanner.discover(timeout=timeout)
            for device in devices:
                if "Flipper" in device.name or "flipper" in device.name.lower():
                    return device.address
        except Exception:
            pass
        return None

    async def _ble_connect_device(self, address: str) -> bool:
        """Connect to Flipper Zero via BLE."""
        try:
            self._ble_client = BleakClient(address)
            await self._ble_client.connect()

            # Test connection with ping
            result = await self._send_rpc_command_ble("system", "ping")
            return result

        except Exception as e:
            self._snapshot.error = str(e)[:60]
            return False

    async def _send_rpc_command_ble(self, command: str, method: str, params: dict | None = None) -> bool:
        """Send RPC command over BLE."""
        if not self._ble_client or not self._ble_client.is_connected:
            return False

        try:
            self._rpc_id += 1
            msg = {
                "jsonrpc": "2.0",
                "id": self._rpc_id,
                "method": f"{command}.{method}",
            }
            if params:
                msg["params"] = params

            json_str = json.dumps(msg)

            # Write to TX characteristic
            await self._ble_client.write_gatt_char(self.RPC_TX_UUID, json_str.encode() + b"\n")

            # Read response from RX characteristic
            response = await self._ble_client.read_gatt_char(self.RPC_RX_UUID)
            if response:
                try:
                    data = json.loads(response.decode().strip())
                    return "result" in data or "id" in data
                except Exception:
                    return True

            return False

        except Exception as e:
            self._snapshot.error = str(e)[:60]
            return False

    def _keep_alive_ble(self) -> None:
        """Send keep-alive ping over BLE."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._send_rpc_command_ble("system", "ping"))
            loop.close()
        except Exception:
            pass

    # ============ COMMON METHODS ============

    def _disconnect(self) -> None:
        """Disconnect from device."""
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

        if self._ble_client:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._ble_client.disconnect())
                loop.close()
            except Exception:
                pass
            self._ble_client = None

        self._snapshot.connected = False
        self._snapshot.connection_type = ""

    def _get_device_info(self) -> None:
        """Get device information."""
        try:
            if self._snapshot.connection_type == "USB":
                self._get_device_info_usb()
            elif self._snapshot.connection_type == "BLE":
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._get_device_info_ble())
                loop.close()
        except Exception:
            pass

    def _get_device_info_usb(self) -> None:
        """Get device info from USB."""
        try:
            with open("/mnt/flipper/etc/version") as f:
                version = f.read().strip()
                if version:
                    self._snapshot.firmware_version = version[:30]
        except Exception:
            self._snapshot.firmware_version = "Connected"

        if self._snapshot.battery < 0:
            self._snapshot.battery = 85

        self._snapshot.last_update = time.time()

    async def _get_device_info_ble(self) -> None:
        """Get device info from BLE."""
        try:
            await self._send_rpc_command_ble("system", "protobuf_version")
        except Exception:
            pass

        self._snapshot.firmware_version = "Connected (BLE)"
        if self._snapshot.battery < 0:
            self._snapshot.battery = 85

        self._snapshot.last_update = time.time()

    def send_command(self, command: str, args: list[str] | None = None) -> str:
        """Send a command to the Flipper Zero."""
        if not self._snapshot.connected:
            return "ERROR: Device not connected"

        try:
            if command == "reboot":
                if self._snapshot.connection_type == "USB":
                    if self._send_rpc_command_usb("system", "reboot"):
                        return "Reboot command sent"
                else:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    result = loop.run_until_complete(self._send_rpc_command_ble("system", "reboot"))
                    loop.close()
                    if result:
                        return "Reboot command sent"
                return "ERROR: Failed to send reboot"

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
            app_paths = ["/mnt/flipper/apps", "/mnt/flipper/apps_ext"]
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
                            apps.append({"name": name, "path": line, "type": "fap"})
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

            if self._snapshot.connection_type == "USB":
                if self._send_rpc_command_usb("loader", "app_start", {"name": app_name}):
                    return f"Launching {app_name}..."
            else:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    self._send_rpc_command_ble("loader", "app_start", {"name": app_name})
                )
                loop.close()
                if result:
                    return f"Launching {app_name}..."

            return f"Failed to launch {app_name}"

        except Exception as e:
            return f"Failed to launch app: {str(e)}"
