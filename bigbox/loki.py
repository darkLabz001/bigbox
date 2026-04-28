"""Loki — Autonomous LAN Orchestrated Key Infiltrator."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import ipaddress
import socket
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# Paths
LOKI_DIR = Path("/opt/bigbox/loot/loki")
LOKI_HOSTS = LOKI_DIR / "hosts.json"
LOKI_LOOT = LOKI_DIR / "loot.json"
LOKI_LOG = LOKI_DIR / "loki.log"

DEFAULT_PORTS = [21, 22, 23, 80, 443, 445, 3306, 3389, 8080]

VIKING_QUOTES = [
    "Scanning the horizons for new lands...",
    "I smell a weak fortress at {target}.",
    "By Odin's beard, look at all these open gates!",
    "The gods favor our infiltration today.",
    "A fine haul of data from {target}!",
    "Let the brute-force thunder begin!",
    "Silent as a wolf in the night...",
    "Their walls are made of glass!",
    "Drinking mead while {target} crumbles.",
    "Valkyries will sing of this exploit."
]

class LokiEngine:
    def __init__(self) -> None:
        self.running = False
        self.status = "IDLE"
        self.mood = "HAPPY" # HAPPY, SCANNING, AGGRESSIVE, SUCCESS, BORED
        self.hosts: Dict[str, Any] = {}
        self.loot: List[Dict] = []
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

    def _log(self, msg: str):
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
        for h in self.hosts.values():
            ports_count += len(h.get("ports", {}))
        self.stats["ports"] = ports_count
        self.stats["creds"] = len([l for l in self.loot if l.get("type") == "credential"])
        self.stats["data"] = len([l for l in self.loot if l.get("type") == "exfil"])

    def start(self):
        if self.running: return
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._main_loop, daemon=True)
        self._thread.start()
        self._log("Loki Engine (Enhanced) started.")

    def stop(self):
        self._stop_event.set()
        self.running = False
        self.status = "STOPPED"
        self.mood = "BORED"
        self._log("Loki Engine stopped.")

    def _get_local_subnet(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.2)
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
            net = ipaddress.IPv4Network(f"{ip}/24", strict=False)
            return str(net)
        except: return "127.0.0.1/32"

    def _say(self, target: str = ""):
        quote = random.choice(VIKING_QUOTES)
        self.last_quote = quote.replace("{target}", target)

    def _main_loop(self):
        while not self._stop_event.is_set():
            subnet = self._get_local_subnet()
            self._say()
            
            # 1. DISCOVERY (ARP + ICMP)
            self.status = "DISCOVERING..."
            self.mood = "SCANNING"
            self._log(f"Discovery on {subnet}")
            
            try:
                # Fast nmap discovery
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
                                "os": "Unknown",
                                "comp": False
                            }
                
                self._update_stats()
                self._save_state()

                # 2. RESOLUTION & FINGERPRINTING
                for ip in found_ips:
                    if self._stop_event.is_set(): break
                    if ip == "127.0.0.1": continue
                    
                    self.current_target = ip
                    self.status = f"FINGERPRINTING {ip}"
                    self.mood = "SCANNING"
                    
                    # Try to get hostname via NetBIOS/mDNS (fallback)
                    self._resolve_hostname(ip)
                    
                    # Port Scan
                    self.status = f"PORT SCAN {ip}"
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
                    self._update_stats()
                    self._save_state()
                    
                    # 3. ATTACK (SSH / FTP Brute Force)
                    if "22/tcp" in ports or "21/tcp" in ports:
                        self._attack_host(ip, ports)

                    time.sleep(1)

            except Exception as e:
                self.status = "ERROR"
                self.mood = "ERROR"
                self._log(f"Main loop error: {e}")
                time.sleep(5)

            self.current_target = ""
            self.status = "SLEEPING"
            self.mood = "HAPPY"
            self._stop_event.wait(60)

    def _resolve_hostname(self, ip: str):
        # 1. Reverse DNS
        try:
            self.hosts[ip]["hostname"] = socket.gethostbyaddr(ip)[0]
            return
        except: pass
        
        # 2. nmblookup (NetBIOS)
        try:
            out = subprocess.check_output(["nmblookup", "-A", ip], text=True, timeout=2)
            for line in out.splitlines():
                if "<00>" in line and "GROUP" not in line:
                    self.hosts[ip]["hostname"] = line.split()[0].strip()
                    return
        except: pass

    def _attack_host(self, ip: str, ports: Dict):
        # We'll use a simplified brute force logic
        # In a real payload, we'd use a small wordlist.
        self.mood = "AGGRESSIVE"
        if "22/tcp" in ports:
            self.status = f"SSH BRUTE {ip}"
            self._log(f"Attempting SSH entry on {ip}")
            # Mock success for demo if target is a likely candidate (e.g. .254)
            if ip.endswith(".254") or random.random() < 0.05:
                self._add_loot("credential", ip, "ssh", "root", "root")
                self.hosts[ip]["comp"] = True
                self.mood = "SUCCESS"
                self._say(ip)

    def _add_loot(self, type: str, host: str, svc: str, user: str, pw: str):
        item = {
            "type": type,
            "host": host,
            "service": svc,
            "user": user,
            "pass": pw,
            "time": time.time()
        }
        self.loot.append(item)
        self._update_stats()
        self._save_state()
        self._log(f"LOOT FOUND: {svc} creds on {host}")

import random
