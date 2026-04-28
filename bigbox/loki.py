"""Loki — Autonomous Network Reconnaissance Engine."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import ipaddress
import socket
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

# Paths
LOKI_DIR = Path("/opt/bigbox/loot/loki")
LOKI_HOSTS = LOKI_DIR / "hosts.json"
LOKI_LOG = LOKI_DIR / "loki.log"

class LokiEngine:
    def __init__(self) -> None:
        self.running = False
        self.status = "IDLE"
        self.hosts: Dict[str, Any] = {}
        self.current_target = ""
        self.last_update = time.time()
        
        LOKI_DIR.mkdir(parents=True, exist_ok=True)
        self._load_hosts()
        
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _log(self, msg: str):
        with LOKI_LOG.open("a") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

    def _load_hosts(self):
        if LOKI_HOSTS.exists():
            try:
                self.hosts = json.loads(LOKI_HOSTS.read_text())
            except:
                self.hosts = {}

    def _save_hosts(self):
        LOKI_HOSTS.write_text(json.dumps(self.hosts, indent=2))

    def start(self):
        if self.running: return
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        self._log("Loki Engine started.")

    def stop(self):
        self._stop_event.set()
        self.running = False
        self.status = "STOPPED"
        self._log("Loki Engine stopped.")

    def _get_local_subnet(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.2)
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
            net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
            return str(net)
        except:
            return "127.0.0.1/32"

    def _main_loop(self):
        while not self._stop_event.is_set():
            subnet = self._get_local_subnet()
            
            # 1. SCAN PHASE
            self.status = f"SCANNING {subnet}"
            self._log(f"Starting host discovery on {subnet}")
            
            try:
                # Fast nmap scan for live hosts
                cmd = ["nmap", "-sn", "-T4", subnet]
                out = subprocess.check_output(cmd, text=True)
                
                # Parse IPs: "Nmap scan report for 192.168.1.1"
                found_ips = []
                for line in out.splitlines():
                    if "Nmap scan report for" in line:
                        ip = line.split()[-1].strip("()")
                        found_ips.append(ip)
                        if ip not in self.hosts:
                            self.hosts[ip] = {
                                "first_seen": time.time(),
                                "last_seen": time.time(),
                                "ports": {},
                                "os": "Unknown"
                            }
                
                self._log(f"Discovery found {len(found_ips)} hosts.")
                self._save_hosts()

                # 2. TARGET PHASE
                for ip in found_ips:
                    if self._stop_event.is_set(): break
                    if ip == "127.0.0.1": continue
                    
                    self.current_target = ip
                    self.status = f"PROBING {ip}"
                    self._log(f"Service scan on {ip}")
                    
                    # Detailed scan on specific host
                    # -F: fast mode (100 ports)
                    cmd = ["nmap", "-F", "-sV", "--version-light", ip]
                    out = subprocess.check_output(cmd, text=True)
                    
                    # Parse ports
                    # 22/tcp open  ssh     OpenSSH 8.2p1
                    ports = {}
                    for line in out.splitlines():
                        if "/tcp" in line and "open" in line:
                            parts = line.split()
                            port_proto = parts[0]
                            service = parts[2]
                            version = " ".join(parts[3:]) if len(parts) > 3 else "Unknown"
                            ports[port_proto] = {"service": service, "version": version}
                    
                    self.hosts[ip]["ports"] = ports
                    self.hosts[ip]["last_seen"] = time.time()
                    self._save_hosts()
                    
                    # 3. ATTACK/INTERACT PHASE (Stub for now)
                    if "22/tcp" in ports:
                        self.status = f"HYDRA SSH {ip}"
                        self._log(f"Host {ip} has SSH open. Ready for brute-force.")
                    
                    time.sleep(2)

            except Exception as e:
                self._log(f"Error in main loop: {e}")
                self.status = "ERROR"
                time.sleep(5)

            self.current_target = ""
            self.status = "SLEEPING"
            self._stop_event.wait(60) # Wait 1 min before next cycle
