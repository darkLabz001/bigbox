"""Loki — Autonomous LAN Orchestrated Key Infiltrator (v2)."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import ipaddress
import socket
import re
import random
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# Paths
LOKI_DIR = Path("/opt/bigbox/loot/loki")
LOKI_HOSTS = LOKI_DIR / "hosts.json"
LOKI_LOOT = LOKI_DIR / "loot.json"
LOKI_LOG = LOKI_DIR / "loki.log"

DEFAULT_PORTS = [21, 22, 23, 80, 443, 445, 3306, 3389, 8080]

MOODS = {
    "HAPPY": ["(^_^)", "(^o^)", "(^u^)"],
    "SCANNING": ["(o_o)", "(O_O)", "(._.)"],
    "AGGRESSIVE": ["(>_<)", "(;_;)", "(@_@)"],
    "SUCCESS": ["(^v^)", "(☆_☆)", "($.$)"],
    "ERROR": ["(x_x)", "(X_X)", "(;-;)"],
    "BORED": ["(-_-)", "(~_~)", "(u_u)"]
}

class LokiEngine:
    def __init__(self) -> None:
        self.running = False
        self.status = "IDLE"
        self.mood = "HAPPY"
        self.face_frame = 0
        self.hosts: Dict[str, Any] = {}
        self.loot: List[Dict] = []
        self.event_log: List[str] = []
        self.current_target = ""
        self.last_quote = "Loki is ready for war."
        
        # Stats for UI
        self.stats = {
            "targets": 0,
            "ports": 0,
            "vulns": 0,
            "creds": 0,
            "zombies": 0,
            "data": 0
        }
        
        LOKI_DIR.mkdir(parents=True, exist_ok=True)
        self._load_state()
        
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _log_event(self, msg: str):
        with self._lock:
            ts = datetime.now().strftime("%H:%M")
            self.event_log.append(f"[{ts}] {msg}")
            if len(self.event_log) > 50:
                self.event_log.pop(0)
            
            with LOKI_LOG.open("a") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

    def _load_state(self):
        if LOKI_HOSTS.exists():
            try: self.hosts = json.loads(LOKI_HOSTS.read_text())
            except: self.hosts = {}
        if LOKI_LOOT.exists():
            try: self.loot = json.loads(LOKI_LOOT.read_text())
            except: self.loot = []
        self._update_stats()

    def _save_state(self):
        LOKI_HOSTS.write_text(json.dumps(self.hosts, indent=2))
        LOKI_LOOT.write_text(json.dumps(self.loot, indent=2))

    def _update_stats(self):
        self.stats["targets"] = len(self.hosts)
        ports_count = 0
        vulns_count = 0
        zombies_count = 0
        for h in self.hosts.values():
            ports_count += len(h.get("ports", {}))
            vulns_count += len(h.get("vulns", []))
            if h.get("comp"): zombies_count += 1
            
        self.stats["ports"] = ports_count
        self.stats["vulns"] = vulns_count
        self.stats["zombies"] = zombies_count
        self.stats["creds"] = len([l for l in self.loot if l.get("type") == "credential"])
        self.stats["data"] = len([l for l in self.loot if l.get("type") == "exfil"])

    def start(self):
        if self.running: return
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        self._log_event("Loki Engine engaged.")

    def stop(self):
        self._stop_event.set()
        self.running = False
        self.status = "STOPPED"
        self.mood = "BORED"
        self._log_event("Loki Engine disengaged.")

    def _get_local_subnet(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.2)
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
            net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
            return str(net)
        except: return "127.0.0.1/32"

    def _main_loop(self):
        while not self._stop_event.is_set():
            subnet = self._get_local_subnet()
            
            # 1. DISCOVERY
            self.status = "DISCOVERING"
            self.mood = "SCANNING"
            self._log_event(f"Scanning subnet: {subnet}")
            
            try:
                cmd = ["nmap", "-sn", "-T4", subnet]
                out = subprocess.check_output(cmd, text=True)
                
                found_ips = []
                for line in out.splitlines():
                    if "Nmap scan report for" in line:
                        ip = line.split()[-1].strip("()")
                        found_ips.append(ip)
                        if ip not in self.hosts:
                            self.hosts[ip] = {
                                "hostname": "Unknown",
                                "first_seen": time.time(),
                                "last_seen": time.time(),
                                "ports": {},
                                "vulns": [],
                                "os": "Unknown",
                                "comp": False
                            }
                
                self._update_stats()
                self._save_state()

                # 2. PROBING
                for ip in found_ips:
                    if self._stop_event.is_set(): break
                    if ip == "127.0.0.1": continue
                    
                    self.current_target = ip
                    self.status = f"PROBING {ip}"
                    self.mood = "SCANNING"
                    
                    # Resolve Hostname
                    self._resolve_hostname(ip)
                    
                    # Port Scan & Version Detection
                    ports_str = ",".join(map(str, DEFAULT_PORTS))
                    cmd = ["nmap", "-p", ports_str, "-sV", "--version-light", "-T4", ip]
                    out = subprocess.check_output(cmd, text=True)
                    
                    ports = {}
                    for line in out.splitlines():
                        if "/tcp" in line and "open" in line:
                            parts = line.split()
                            port = parts[0]
                            svc = parts[2]
                            ver = " ".join(parts[3:]) if len(parts) > 3 else "Unknown"
                            ports[port] = {"service": svc, "version": ver}
                    
                    self.hosts[ip]["ports"] = ports
                    self.hosts[ip]["last_seen"] = time.time()
                    
                    # Vuln Scan (NSE Stubs)
                    if "80/tcp" in ports:
                        self.status = f"VULN SCAN {ip}"
                        self._log_event(f"Checking web vulnerabilities on {ip}")
                        # Mock vulnerability check
                        if random.random() < 0.1:
                            self.hosts[ip]["vulns"].append("CVE-2023-XXXXX (Web)")
                    
                    self._update_stats()
                    self._save_state()
                    
                    # 3. ATTACK
                    if "22/tcp" in ports or "21/tcp" in ports:
                        self._attack_host(ip, ports)

                    time.sleep(1)

            except Exception as e:
                self.status = "ERROR"
                self.mood = "ERROR"
                self._log_event(f"Engine Error: {e}")
                time.sleep(5)

            self.current_target = ""
            self.status = "SLEEPING"
            self.mood = "HAPPY"
            self._stop_event.wait(30)

    def _resolve_hostname(self, ip: str):
        try:
            self.hosts[ip]["hostname"] = socket.gethostbyaddr(ip)[0]
        except:
            try:
                out = subprocess.check_output(["nmblookup", "-A", ip], text=True, timeout=1)
                for line in out.splitlines():
                    if "<00>" in line and "GROUP" not in line:
                        self.hosts[ip]["hostname"] = line.split()[0].strip()
                        break
            except: pass

    def _attack_host(self, ip: str, ports: Dict):
        self.mood = "AGGRESSIVE"
        if "22/tcp" in ports:
            self.status = f"BRUTE SSH {ip}"
            self._log_event(f"Brute forcing SSH on {ip}...")
            # Success simulation
            if ip.endswith(".254") or random.random() < 0.05:
                self._add_loot("credential", ip, "ssh", "admin", "admin")
                self.hosts[ip]["comp"] = True
                self.mood = "SUCCESS"
                self._log_event(f"COMPROMISED: {ip} via SSH!")

    def _add_loot(self, type: str, host: str, svc: str, user: str, pw: str):
        item = {
            "type": type, "host": host, "service": svc,
            "user": user, "pass": pw, "time": time.time()
        }
        self.loot.append(item)
        self._update_stats()
        self._save_state()

    def update_animation(self):
        """Called by UI to tick face animation."""
        self.face_frame = (self.face_frame + 1) % 3

    def get_face(self) -> str:
        return MOODS[self.mood][self.face_frame]
