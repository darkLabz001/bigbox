"""RetroAchievements integration — credential persistence + emulator config.

Auth model: the user gives their RA username + password to the web UI;
we hit https://retroachievements.org/dorequest.php?r=login&u=...&p=...
which returns a JSON body with a Token (not the password) on success.
We persist {username, token} to /etc/bigbox/retroachievements.json
(mode 0600), outside /opt/bigbox so the OTA's git reset never wipes it.

Before launching mGBA we patch ~/.config/mgba/config.ini with
cheevosUsername= and cheevosToken=. mGBA picks those up at startup and
unlocks achievements as the user plays.

DuckStation has its own config schema and its own auth flow, so v1 only
wires RA into mGBA. PS1 still launches without achievements.
"""
from __future__ import annotations

import configparser
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


RA_API = "https://retroachievements.org/dorequest.php"
CONFIG_DIR = Path("/etc/bigbox")
CRED_PATH = CONFIG_DIR / "retroachievements.json"

# mGBA's per-user config. We write here as root (bigbox runs as root)
# and mGBA picks it up at launch.
MGBA_CONFIG_DIR = Path("/root/.config/mgba")
MGBA_CONFIG = MGBA_CONFIG_DIR / "config.ini"


@dataclass
class RACreds:
    username: str
    token: str

    def to_dict(self) -> dict:
        return {"username": self.username, "token": self.token}


# ---------- credential persistence ----------

def load_creds() -> Optional[RACreds]:
    if not CRED_PATH.exists():
        return None
    try:
        data = json.loads(CRED_PATH.read_text())
        u = data.get("username")
        t = data.get("token")
        if u and t:
            return RACreds(username=u, token=t)
    except Exception:
        pass
    return None


def save_creds(creds: RACreds) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(creds.to_dict())
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


# ---------- API ----------

def login(username: str, password: str) -> tuple[bool, str, Optional[RACreds]]:
    """Hit RA's dorequest login endpoint. Returns (ok, message, creds)."""
    try:
        r = requests.get(
            RA_API,
            params={"r": "login", "u": username, "p": password},
            timeout=10,
        )
    except Exception as e:
        return False, f"network error: {type(e).__name__}: {e}", None

    if r.status_code != 200:
        return False, f"retroachievements returned HTTP {r.status_code}", None
    try:
        data = r.json()
    except Exception:
        return False, "couldn't parse response", None

    if not data.get("Success"):
        return False, data.get("Error") or "login failed", None
    token = data.get("Token") or data.get("token")
    if not token:
        return False, "no token in response", None
    creds = RACreds(username=username, token=token)
    return True, f"signed in as {username}", creds


# ---------- mGBA config patcher ----------

def apply_to_mgba_config(creds: RACreds) -> None:
    """Write cheevosUsername / cheevosToken to mGBA's config.ini, leaving
    every other setting alone. Idempotent."""
    MGBA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    cp = configparser.ConfigParser()
    cp.optionxform = str  # preserve case for keys

    if MGBA_CONFIG.exists():
        try:
            cp.read(MGBA_CONFIG)
        except Exception:
            # If the existing file is malformed, start fresh — better
            # than refusing to apply creds.
            cp = configparser.ConfigParser()
            cp.optionxform = str

    # mGBA stores cheevos creds under [ports.qt] and [ports.sdl] depending
    # on the binary. Write to both so launching either picks them up.
    for section in ("ports.qt", "ports.sdl"):
        if not cp.has_section(section):
            cp.add_section(section)
        cp.set(section, "cheevosUsername", creds.username)
        cp.set(section, "cheevosToken", creds.token)
        cp.set(section, "cheevosEnabled", "1")

    with MGBA_CONFIG.open("w") as f:
        cp.write(f, space_around_delimiters=False)


def remove_from_mgba_config() -> None:
    """Strip cheevosUsername/Token from mGBA's config — used by logout."""
    if not MGBA_CONFIG.exists():
        return
    cp = configparser.ConfigParser()
    cp.optionxform = str
    try:
        cp.read(MGBA_CONFIG)
    except Exception:
        return
    for section in ("ports.qt", "ports.sdl"):
        if cp.has_section(section):
            for k in ("cheevosUsername", "cheevosToken"):
                cp.remove_option(section, k)
            cp.set(section, "cheevosEnabled", "0")
    try:
        with MGBA_CONFIG.open("w") as f:
            cp.write(f, space_around_delimiters=False)
    except Exception:
        pass
