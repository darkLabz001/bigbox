"""WiGLE integration — credential persistence, CSV writer, upload.

WiGLE auth model: HTTP Basic with the user's API Name + API Token (NOT
their wigle.net username/password). Tokens are minted at
https://wigle.net/account.

Credentials are stored at /etc/bigbox/wigle.json with mode 0600 so they
survive OTA updates (the OTA git reset only touches /opt/bigbox).
"""
from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests


WIGLE_API_BASE = "https://api.wigle.net/api/v2"
CONFIG_DIR = Path("/etc/bigbox")
CRED_PATH = CONFIG_DIR / "wigle.json"


# ---------- credential persistence ----------

@dataclass
class WigleCreds:
    api_name: str
    api_token: str

    def basic_tuple(self) -> tuple[str, str]:
        return (self.api_name, self.api_token)


def load_creds() -> Optional[WigleCreds]:
    if not CRED_PATH.exists():
        return None
    try:
        data = json.loads(CRED_PATH.read_text())
        name = data.get("api_name")
        tok = data.get("api_token")
        if name and tok:
            return WigleCreds(api_name=name, api_token=tok)
    except Exception:
        pass
    return None


def save_creds(creds: WigleCreds) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({
        "api_name": creds.api_name,
        "api_token": creds.api_token,
    })
    # Write+chmod atomically so the file is never world-readable, even
    # for a moment.
    tmp = CRED_PATH.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CRED_PATH)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def clear_creds() -> None:
    try:
        CRED_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def validate_creds(creds: WigleCreds) -> tuple[bool, str]:
    """Hit WiGLE's profile endpoint with the creds. Returns (ok, message)."""
    try:
        r = requests.get(
            f"{WIGLE_API_BASE}/profile/user",
            auth=creds.basic_tuple(),
            timeout=10,
        )
    except Exception as e:
        return False, f"network error: {type(e).__name__}: {e}"
    if r.status_code == 200:
        try:
            data = r.json()
            if data.get("success"):
                user = data.get("user") or "(unknown)"
                return True, f"signed in as {user}"
            return False, data.get("message") or "unknown wigle response"
        except Exception:
            return True, "signed in"
    if r.status_code == 401:
        return False, "invalid API name / token (401)"
    return False, f"wigle returned HTTP {r.status_code}"


# ---------- CSV writer (WiGLE-1.4 format) ----------

WIGLE_CSV_COLUMNS = (
    "MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,"
    "CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type"
)


def _csv_escape(s: str) -> str:
    # Replace embedded commas/newlines/quotes the same way the reference
    # WiGLE Wi-Fi app does — quote the field if it has any special chars.
    if any(ch in s for ch in (",", "\n", "\r", '"')):
        return '"' + s.replace('"', '""') + '"'
    return s


def wigle_csv_header() -> str:
    """The WiGLE-1.4 'pre-header' line + column header."""
    try:
        host = socket.gethostname()
    except Exception:
        host = "bigbox"
    pre = (
        "WigleWifi-1.4,"
        f"appRelease=bigbox,"
        f"model=GamePi43,"
        f"release=linux,"
        f"device={host},"
        "display=800x480,"
        "board=raspberrypi,"
        "brand=bigbox,"
        "star=Sol,"
        "body=3,"
        "subBody=0"
    )
    return pre + "\n" + WIGLE_CSV_COLUMNS + "\n"


def now_wigle_timestamp() -> str:
    # WiGLE accepts "YYYY-MM-DD HH:MM:SS" UTC.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def wigle_csv_row(
    *,
    mac: str,
    ssid: str,
    authmode: str,
    first_seen: str,
    channel: int | str,
    rssi: int,
    lat: float,
    lon: float,
    alt_m: float,
    accuracy_m: float,
    obs_type: str,  # "WIFI" or "BT" or "BLE"
) -> str:
    fields = [
        _csv_escape(mac),
        _csv_escape(ssid or ""),
        _csv_escape(authmode or "[]"),
        _csv_escape(first_seen),
        str(channel) if channel is not None else "0",
        str(int(rssi)),
        f"{lat:.7f}",
        f"{lon:.7f}",
        f"{alt_m:.1f}",
        f"{accuracy_m:.1f}",
        obs_type,
    ]
    return ",".join(fields) + "\n"


# ---------- upload ----------

def upload(path: Path, creds: WigleCreds) -> tuple[bool, str]:
    """POST a CSV (or zip) to WiGLE. Returns (ok, message)."""
    if not path.exists():
        return False, f"missing file: {path}"
    try:
        with path.open("rb") as f:
            r = requests.post(
                f"{WIGLE_API_BASE}/file/upload",
                auth=creds.basic_tuple(),
                files={"file": (path.name, f, "text/csv")},
                # WiGLE's docs allow a `donate` query param; default to
                # "false" so we don't surprise the user.
                data={"donate": "false"},
                timeout=120,
            )
    except Exception as e:
        return False, f"network error: {type(e).__name__}: {e}"
    if r.status_code == 200:
        try:
            data = r.json()
            if data.get("success"):
                return True, data.get("message") or "uploaded"
            return False, data.get("message") or "wigle rejected upload"
        except Exception:
            return True, "uploaded"
    if r.status_code == 401:
        return False, "auth failed (401) — re-login"
    return False, f"wigle returned HTTP {r.status_code}: {r.text[:120]}"
