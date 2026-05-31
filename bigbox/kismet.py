"""Kismet integration for Wardriving.

Kismet runs as a background service and provides a REST API.
This module helps manage the kismet process and fetch observed devices.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import requests
from pathlib import Path

class KismetManager:
    def __init__(self, port: int = 2501):
        self.port = port
        self._proc: subprocess.Popen | None = None
        self._stop = False
        self.base_url = f"http://localhost:{port}"

    def start(self, ifaces: list[str]) -> bool:
        """Start kismet with specified interfaces."""
        # Check if kismet is already running
        try:
            r = requests.get(f"{self.base_url}/system/status.json", timeout=1)
            if r.status_code == 200:
                # Already running, just use it
                return True
        except:
            pass

        # Start kismet
        # --no-wrapper: don't use the suid wrapper
        # --no-daemon: run in foreground
        # -c <iface>: capture on interface
        cmd = ["kismet", "--no-wrapper", "--no-daemon", "--port", str(self.port)]
        for iface in ifaces:
            cmd.extend(["-c", iface])
        
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL
            )
            # Wait for it to start
            for _ in range(10):
                time.sleep(1)
                try:
                    r = requests.get(f"{self.base_url}/system/status.json", timeout=1)
                    if r.status_code == 200:
                        return True
                except:
                    pass
            return False
        except Exception:
            return False

    def stop(self):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except:
                self._proc.kill()
            self._proc = None

    def get_devices(self, since_ts: int = 0) -> list[dict]:
        """Fetch devices discovered or updated since since_ts."""
        # /devices/views/all/devices.json
        # Kismet API is complex, but this view gives us what we need.
        try:
            # We want WIFI and BT devices
            # filter=kismet.device.base.last_time > {since_ts}
            url = f"{self.base_url}/devices/views/all/devices.json"
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return []

    def get_wifi_devices(self, last_ts: int = 0) -> list[dict]:
        """Fetch Wi-Fi devices updated since last_ts."""
        try:
            # We filter by 'last_time' to avoid processing the same devices repeatedly.
            # Kismet uses Unix timestamps.
            url = f"{self.base_url}/devices/views/phydot11_accesspoints/devices.json"
            if last_ts > 0:
                # Kismet REST API filtering syntax
                url += f"?filter=kismet.device.base.last_time > {last_ts}"
            
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return []

    def get_bt_devices(self, last_ts: int = 0) -> list[dict]:
        """Fetch Bluetooth devices updated since last_ts."""
        try:
            url = f"{self.base_url}/devices/views/bluetooth_devices/devices.json"
            if last_ts > 0:
                url += f"?filter=kismet.device.base.last_time > {last_ts}"
                
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return []
