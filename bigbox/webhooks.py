"""Webhook integration — send captures to a remote server.
Configuration stored at /etc/bigbox/webhook.json
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional
import requests

CONFIG_DIR = Path("/etc/bigbox")
WEBHOOK_PATH = CONFIG_DIR / "webhook.json"

def load_webhook_url() -> Optional[str]:
    if not WEBHOOK_PATH.exists():
        return None
    try:
        data = json.loads(WEBHOOK_PATH.read_text())
        return data.get("url")
    except Exception:
        return None

def save_webhook_url(url: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"url": url})
    tmp = WEBHOOK_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(payload)
        os.replace(tmp, WEBHOOK_PATH)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

def send_file(path: str) -> tuple[bool, str]:
    """POST a file to the configured webhook URL.
    Handles standard generic webhooks and Discord/Slack-style formats.
    """
    url = load_webhook_url()
    if not url:
        return False, "Webhook URL not configured"
    
    p = Path(path)
    if not p.exists():
        return False, f"File not found: {path}"

    try:
        with p.open("rb") as f:
            # Detect mimetype
            files = {"file": (p.name, f)}
            # Optional: Add metadata/content for Discord
            data = {"content": f"New capture from DaRkb0x: {p.name}"}
            
            r = requests.post(url, files=files, data=data, timeout=30)
            
        if r.status_code in (200, 201, 204):
            return True, "Sent successfully"
        else:
            return False, f"Server returned HTTP {r.status_code}"
    except Exception as e:
        return False, f"Error: {type(e).__name__}: {e}"
