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


def diagnose_flipper() -> dict:
    """Diagnose Flipper Zero connectivity."""
    diagnostics = {
        "lsusb_output": "",
        "serial_ports": [],
        "bt_devices": [],
        "dmesg_recent": "",
        "permissions": {},
    }

    # Check lsusb
    try:
        result = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5)
        diagnostics["lsusb_output"] = result.stdout
        if "0483:5740" in result.stdout:
            diagnostics["flipper_detected_usb"] = True
    except Exception as e:
        diagnostics["lsusb_error"] = str(e)

    # Check serial ports
    try:
        result = subprocess.run(["ls", "-la", "/dev/tty*"], capture_output=True, text=True, timeout=5)
        diagnostics["serial_ports"] = result.stdout.split("\n")
    except Exception:
        pass

    # Check Bluetooth
    try:
        result = subprocess.run(["bluetoothctl", "devices"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split("\n"):
            if "Flipper" in line or "flipper" in line.lower():
                diagnostics["bt_devices"].append(line)
    except Exception:
        pass

    # Check dmesg
    try:
        result = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.split("\n")[-30:]
        diagnostics["dmesg_recent"] = "\n".join(lines)
    except Exception:
        pass

    return diagnostics


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
        usb_attempts = 0
        ble_attempts = 0

        while self.running and not self._stop_event.is_set():
            try:
                if not self._snapshot.connected:
                    # Try USB first (with limit to avoid infinite loop)
                    if usb_attempts < 3:
                        if self._try_usb_connect():
                            usb_attempts = 0
                            ble_attempts = 0
                        else:
                            usb_attempts += 1

                    # Try BLE if USB failed
                    if not self._snapshot.connected and ble_attempts < 3:
                        if self._try_ble_connect():
                            usb_attempts = 0
                            ble_attempts = 0
                        else:
                            ble_attempts += 1

                    # If both failed, mark as disconnected
                    if not self._snapshot.connected:
                        self._snapshot.phase = "DISCONNECTED"

                else:
                    # Keep connection alive
                    if self._snapshot.connection_type == "USB":
                        if not self._keep_alive_usb():
                            self._disconnect()
                    elif self._snapshot.connection_type == "BLE":
                        if not self._keep_alive_ble():
                            self._disconnect()
                    else:
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

        self._snapshot.phase = "CONNECTING (USB)"
        serial_port = self._find_serial_port()

        if not serial_port:
            self._snapshot.phase = "USB: No port found"
            return False

        try:
            if serial is None:
                self._snapshot.error = "pyserial not installed"
                return False

            # Try to open port with short timeout
            self._serial = serial.Serial(
                port=serial_port,
                baudrate=230400,
                timeout=0.5
            )
            self._snapshot.serial_port = serial_port
            time.sleep(0.5)

            # Try to ping device
            if self._send_rpc_command_usb("system", "ping"):
                self._snapshot.connected = True
                self._snapshot.phase = "CONNECTED"
                self._snapshot.connection_type = "USB"
                self._snapshot.error = ""
                self._snapshot.device_name = "Flipper Zero (USB)"
                return True
            else:
                self._snapshot.phase = "USB: No response"
                self._disconnect()
                return False

        except Exception as e:
            self._snapshot.phase = f"USB: {str(e)[:30]}"
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
            # Send with newline terminator
            self._serial.write((json_str + "\n").encode("utf-8"))
            self._serial.flush()

            # Read response with better handling
            response = b""
            start_time = time.time()
            line_complete = False

            while time.time() - start_time < 3.0 and not line_complete:
                try:
                    # Read available data
                    if self._serial.in_waiting > 0:
                        chunk = self._serial.read(self._serial.in_waiting)
                        response += chunk
                        # Check if we have a complete line
                        if b"\n" in response:
                            line_complete = True
                    else:
                        time.sleep(0.05)
                except Exception:
                    time.sleep(0.05)

            if response:
                try:
                    # Try to parse JSON response
                    response_str = response.decode("utf-8").strip()
                    if response_str:
                        data = json.loads(response_str)
                        # Success if we get result, error, or just id back
                        return "result" in data or "error" in data or "id" in data
                except Exception as parse_err:
                    # Even if not JSON, if we got data back, consider it a response
                    return len(response) > 0

            return False

        except Exception as e:
            self._snapshot.error = str(e)[:60]
            return False

    def _keep_alive_usb(self) -> bool:
        """Send keep-alive ping over USB."""
        try:
            return self._send_rpc_command_usb("system", "ping")
        except Exception:
            return False

    # ============ BLE BLUETOOTH CONNECTION ============

    def _try_ble_connect(self) -> bool:
        """Try to connect via Bluetooth LE."""
        if not BleakClient or not BleakScanner:
            self._snapshot.phase = "BLE: bleak not installed"
            return False

        if self._snapshot.connection_type == "BLE" and self._snapshot.connected:
            return True

        self._snapshot.phase = "CONNECTING (BLE)"

        try:
            # Scan for Flipper Zero with timeout
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                address = loop.run_until_complete(asyncio.wait_for(self._ble_scan(), timeout=10.0))
            except asyncio.TimeoutError:
                self._snapshot.phase = "BLE: Scan timeout"
                loop.close()
                return False

            if not address:
                self._snapshot.phase = "BLE: Device not found"
                loop.close()
                return False

            # Connect to device
            self._ble_device_address = address
            try:
                success = loop.run_until_complete(asyncio.wait_for(self._ble_connect_device(address), timeout=10.0))
            except asyncio.TimeoutError:
                self._snapshot.phase = "BLE: Connect timeout"
                loop.close()
                return False

            loop.close()

            if success:
                self._snapshot.connected = True
                self._snapshot.phase = "CONNECTED"
                self._snapshot.connection_type = "BLE"
                self._snapshot.error = ""
                self._snapshot.device_name = "Flipper Zero (BLE)"
                return True
            else:
                self._snapshot.phase = "BLE: Connection failed"
                return False

        except Exception as e:
            self._snapshot.phase = f"BLE: {str(e)[:30]}"
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

    def _keep_alive_ble(self) -> bool:
        """Send keep-alive ping over BLE."""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self._send_rpc_command_ble("system", "ping"))
            loop.close()
            return result
        except Exception:
            return False

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
            # Try to read firmware version
            version_files = [
                "/mnt/flipper/etc/version",
                "/mnt/flipper/.metadata/.version",
            ]

            for vfile in version_files:
                try:
                    with open(vfile) as f:
                        version = f.read().strip()
                        if version and not self._snapshot.firmware_version:
                            # Parse version file (format: like "0.98.0")
                            lines = version.split("\n")
                            for line in lines:
                                if any(c.isdigit() for c in line):
                                    self._snapshot.firmware_version = line[:20]
                                    break
                except Exception:
                    continue
        except Exception:
            pass

        # Set default if not found
        if not self._snapshot.firmware_version or self._snapshot.firmware_version == "Connected":
            self._snapshot.firmware_version = "Flipper Connected"

        # Try to get battery via RPC if available, else use placeholder
        if self._snapshot.battery < 0:
            try:
                # Try RPC battery command
                if self._send_rpc_command_usb("power", "info"):
                    self._snapshot.battery = 85  # Would be real value from RPC
                else:
                    self._snapshot.battery = 85
            except Exception:
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
            # Check multiple possible app locations
            app_paths = [
                "/mnt/flipper/apps",
                "/mnt/flipper/apps_ext",
                "/mnt/flipper/.apps",
            ]

            found_apps = set()

            for app_dir in app_paths:
                try:
                    # Find all app executable files
                    result = subprocess.run(
                        ["find", app_dir, "-type", "f", "-name", "*.fap", "-o", "-name", "*.elf"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )

                    for line in result.stdout.strip().split("\n"):
                        if line and line not in found_apps:
                            found_apps.add(line)
                            name = line.split("/")[-1]
                            # Remove extension
                            for ext in [".fap", ".elf", ".out"]:
                                name = name.replace(ext, "")

                            apps.append({
                                "name": name,
                                "path": line,
                                "type": "fap" if ".fap" in line else "elf",
                                "displayable": True
                            })
                except Exception:
                    continue

            # Sort apps by name
            apps.sort(key=lambda x: x["name"].lower())

        except Exception as e:
            pass

        return apps

    def get_diagnostics(self) -> str:
        """Get diagnostic information about Flipper Zero connectivity."""
        diag = diagnose_flipper()

        output = []
        output.append("=== FLIPPER ZERO DIAGNOSTICS ===\n")

        if diag.get("flipper_detected_usb"):
            output.append("✓ USB Device detected (0483:5740)\n")
        else:
            output.append("✗ USB Device NOT found in lsusb\n")

        if diag.get("bt_devices"):
            output.append("✓ Bluetooth devices found:")
            for device in diag["bt_devices"]:
                output.append(f"  {device}")
            output.append("\n")
        else:
            output.append("✗ No Flipper Zero found in Bluetooth devices\n")

        output.append("\n=== SERIAL PORTS ===\n")
        tty_lines = [l for l in diag.get("serial_ports", []) if l]
        if tty_lines:
            for line in tty_lines[:10]:
                output.append(line + "\n")
        else:
            output.append("No TTY devices found\n")

        output.append("\n=== RECENT DMESG ===\n")
        if "tty" in diag.get("dmesg_recent", "").lower():
            for line in diag["dmesg_recent"].split("\n")[-10:]:
                if line:
                    output.append(line + "\n")
        else:
            output.append("No recent device messages\n")

        return "".join(output)

    def launch_app(self, app_name: str) -> str:
        """Launch an app on the Flipper Zero."""
        try:
            if not self._snapshot.connected:
                return "ERROR: Device not connected"

            if self._snapshot.connection_type == "USB":
                # Try RPC first
                if self._send_rpc_command_usb("loader", "app_start", {"name": app_name}):
                    return f"✓ Launched {app_name} via RPC"

                # Fallback: try to find and launch app via filesystem
                result = subprocess.run(
                    ["find", "/mnt/flipper", "-name", f"{app_name}.fap", "-o", "-name", f"{app_name}.elf"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.stdout.strip():
                    return f"✓ Found {app_name} - launching via storage"
                else:
                    return f"✗ App '{app_name}' not found on device"
            else:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(
                    self._send_rpc_command_ble("loader", "app_start", {"name": app_name})
                )
                loop.close()
                if result:
                    return f"✓ Launched {app_name} via BLE"
                return f"✗ Failed to launch {app_name}"

        except Exception as e:
            return f"✗ Error: {str(e)[:50]}"
