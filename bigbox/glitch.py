"""Glitch Engine — Autonomous Recon & Attack with Personality."""
from __future__ import annotations

import logging
import threading
import time
import subprocess
import re
import socket
import ipaddress
import uvicorn
import random
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("glitch")

@dataclass
class Host:
    ip: str
    hostname: str = ""
    last_seen: float = field(default_factory=time.time)
    services: list[str] = field(default_factory=list)
    status: str = "discovered"
    os_info: str = "Unknown"

class GlitchEngine:
    def __init__(self):
        self.hosts: dict[str, Host] = {}
        self.status_text = "Awakening..."
        self.current_activity = "IDLE"
        self.mood = "CALM" # CALM, EXCITED, AGGRESSIVE, GLITCHY
        self.thought = "Calculating probabilities..."
        self.running = False
        self._thread: threading.Thread | None = None
        self._web_thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.logs: list[str] = []
        self.subnet = self._guess_subnet()
        
        self.app = FastAPI()
        self._setup_routes()

        # Personality data
        self.thoughts = {
            "IDLE": [
                "Silence is where I thrive.", "Waiting for a pulse in the wire...",
                "The grid is vast. I am small but sharp.", "Ghosting through the local subnet...",
                "Scanning for a reason to wake up."
            ],
            "RECON": [
                "Peering through the veil...", "ARP packets flying. Who's home?",
                "Mapping the digital shadows.", "Found a heartbeat. Interesting.",
                "The network is whispering its secrets."
            ],
            "PROBING": [
                "Knocking on doors. Anyone home?", "Fingerprinting the target...",
                "What are you hiding on port 22?", "Services revealed. Vulnerabilities mapped.",
                "Analyzing defenses. Finding the cracks."
            ],
            "ATTACKING": [
                "Infiltrating the stream...", "Brute-force sequence initiated.",
                "They didn't see me coming.", "Data is beautiful when it leaks.",
                "Bypassing logic gates. Almost there..."
            ]
        }

    def _setup_routes(self):
        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            template_path = Path(__file__).resolve().parents[0] / "web" / "glitch" / "index.html"
            if template_path.exists(): return template_path.read_text()
            return "<h1>Glitch Web UI</h1>"

        @self.app.get("/api/status")
        async def get_status():
            with self.lock:
                return {
                    "activity": self.current_activity,
                    "status": self.status_text,
                    "mood": self.mood,
                    "thought": self.thought,
                    "hosts": [asdict(h) for h in self.hosts.values()],
                    "logs": self.logs[-20:]
                }

    def _guess_subnet(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(0.2)
                s.connect(("10.255.255.255", 1))
                ip = s.getsockname()[0]
            return str(ipaddress.IPv4Network(f"{ip}/24", strict=False))
        except: return "172.20.10.0/24"

    def log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.logs.append(f"[{timestamp}] {msg}")
            if len(self.logs) > 100: self.logs.pop(0)
        logger.info(msg)

    def start(self):
        if self.running: return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._web_thread = threading.Thread(
            target=lambda: uvicorn.run(self.app, host="0.0.0.0", port=8888, log_level="error"),
            daemon=True
        )
        self._web_thread.start()

    def _update_thought(self):
        pool = self.thoughts.get(self.current_activity, self.thoughts["IDLE"])
        self.thought = random.choice(pool)

    def _run(self):
        while self.running:
            try:
                self.current_activity = "RECON"
                self._update_thought()
                self._do_recon()
                
                self.current_activity = "PROBING"
                self._update_thought()
                self._do_deep_scan()
                
                self.current_activity = "ATTACKING"
                self._update_thought()
                self._do_attacks()
                
                self.current_activity = "IDLE"
                self._update_thought()
                time.sleep(15)
            except Exception as e:
                self.log(f"Core Error: {e}")
                time.sleep(10)

    def _do_recon(self):
        self.status_text = f"Scanning {self.subnet}..."
        try:
            cmd = ["nmap", "-sn", "-T4", self.subnet]
            out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
            ips = re.findall(r"for (\d+\.\d+\.\d+\.\d+)", out)
            for ip in ips: self._add_host(ip)
            self.log(f"Found {len(ips)} targets.")
        except: pass

    def _add_host(self, ip: str):
        with self.lock:
            if ip not in self.hosts:
                self.hosts[ip] = Host(ip)
                self.log(f"Target Acquired: {ip}")

    def _do_deep_scan(self):
        with self.lock: targets = [h for h in self.hosts.values() if h.status == "discovered"]
        for host in targets:
            if not self.running: break
            self.status_text = f"Fingerprinting {host.ip}..."
            try:
                cmd = ["nmap", "-F", "-T4", host.ip]
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                ports = re.findall(r"(\d+)/tcp\s+open", out)
                with self.lock:
                    host.services = ports
                    host.status = "vulnerable" if ports else "secured"
            except: host.status = "discovered"

    def _do_attacks(self):
        with self.lock: targets = [h for h in self.hosts.values() if h.status == "vulnerable"]
        for host in targets:
            if not self.running: break
            self.status_text = f"Owning {host.ip}..."
            time.sleep(3) # Sim
            with self.lock: host.status = "compromised"
            self.log(f"ROOT ACCESS: {host.ip}")
