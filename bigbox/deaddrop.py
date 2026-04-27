"""Deaddrop — Offline chat room for Rogue AP connections."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs


CHAT_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DEAD DROP :: CHAT</title>
<style>
body{{margin:0;font-family:monospace;background:#000;color:#5ae6aa;display:flex;flex-direction:column;height:100vh;}}
.head{{padding:10px;border-bottom:1px solid #5ae6aa;text-align:center;font-weight:bold;}}
.messages{{flex:1;overflow-y:auto;padding:10px;display:flex;flex-direction:column-reverse;}}
.msg{{margin-bottom:8px;}}
.msg b{{color:#fff;}}
.input-bar{{padding:10px;border-top:1px solid #5ae6aa;display:flex;gap:10px;}}
input{{flex:1;background:#000;border:1px solid #5ae6aa;color:#5ae6aa;padding:8px;font-family:monospace;}}
button{{background:#5ae6aa;color:#000;border:0;padding:8px 16px;font-weight:bold;cursor:pointer;}}
</style>
</head><body>
<div class="head">OFFLINE CHAT :: {ssid}</div>
<div class="messages" id="msgs"></div>
<div class="input-bar">
    <input type="text" id="m" placeholder="Type message..." maxlength="100">
    <button onclick="send()">SEND</button>
</div>
<script>
let last = 0;
async function poll() {{
    try {{
        let r = await fetch('/messages?after=' + last);
        let msgs = await r.json();
        const box = document.getElementById('msgs');
        msgs.forEach(m => {{
            let d = document.createElement('div');
            d.className = 'msg';
            d.innerHTML = `[${{m.time}}] <b>&lt;${{m.user}}&gt;</b> ${{m.text}}`;
            box.prepend(d);
            if (m.id > last) last = m.id;
        }});
    }} catch(e) {{}}
}}
async function send() {{
    let i = document.getElementById('m');
    let t = i.value.trim();
    if(!t) return;
    i.value = '';
    await fetch('/messages', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ text: t }})
    }});
    poll();
}}
setInterval(poll, 2000);
poll();
</script>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_GET(self) -> None:
        if self.path.startswith("/messages"):
            self._send_json(self.server.portal.get_messages(self.path))
        else:
            self._send_html(CHAT_HTML.format(ssid=self.server.portal.ssid))

    def do_POST(self) -> None:
        if self.path == "/messages":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length))
            self.server.portal.add_message(self.client_address[0], data.get("text", ""))
            self.send_response(200)
            self.end_headers()

    def _send_html(self, body: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def _send_json(self, obj: any):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())


class DeadDropServer:
    def __init__(self, ssid: str) -> None:
        self.ssid = ssid
        self.messages = []
        self._lock = threading.Lock()
        self._server = None

    def start(self):
        self._server = ThreadingHTTPServer(("0.0.0.0", 80), _Handler)
        self._server.portal = self
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server:
            self._server.shutdown()

    def add_message(self, ip: str, text: str):
        with self._lock:
            msg = {
                "id": len(self.messages) + 1,
                "user": f"anon_{ip.split('.')[-1]}",
                "text": text[:100],
                "time": datetime.now().strftime("%H:%M")
            }
            self.messages.append(msg)

    def get_messages(self, path: str):
        after = 0
        if "after=" in path:
            after = int(path.split("after=")[1])
        return [m for m in self.messages if m["id"] > after]
