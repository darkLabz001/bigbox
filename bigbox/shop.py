"""BoxShop client — fetch the catalog, install/uninstall payloads.

Catalog source:
    https://github.com/darkLabz001/boxshop  (raw.githubusercontent.com)

The repo's `index.json` lists payloads. Each payload has a per-payload
`manifest.json` that names its files + sha256s. Installs land under a
type-specific root on disk, never paths the manifest controls — so a
manifest can't write to e.g. /etc by lying about `dst`.

Layout on disk:
    /opt/bigbox/cache/shop/index.json     cached catalog
    /opt/bigbox/cache/shop/installed.json bookkeeping for uninstall
    <install_root_for_type>/<id>/...      actual files

State file format (installed.json):
    { "<id>": {"version": "1.0", "files": ["abs/path1", ...]} , ... }
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


BOXSHOP_BASE = "https://raw.githubusercontent.com/darkLabz001/boxshop/main/"
BIGBOX_ROOT = Path("/opt/bigbox")
CACHE_DIR = BIGBOX_ROOT / "cache" / "shop"
INDEX_PATH = CACHE_DIR / "index.json"
INSTALLED_PATH = CACHE_DIR / "installed.json"

# Per-type install roots. The `<id>` segment is appended by _install_dir().
INSTALL_ROOTS: dict[str, Path] = {
    "themes":   BIGBOX_ROOT / "config" / "themes",
    "ble":      BIGBOX_ROOT / "data" / "ble",
    "wireless": BIGBOX_ROOT / "data" / "wireless",
    "recon":    BIGBOX_ROOT / "data" / "recon",
}


def _install_dir(payload_type: str, payload_id: str) -> Optional[Path]:
    root = INSTALL_ROOTS.get(payload_type)
    if root is None:
        return None
    return root / payload_id


def _http_get(url: str, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "bigbox-shop/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> Optional[dict]:
    try:
        with path.open() as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(path)


# ---------- catalog ----------

def refresh() -> tuple[bool, str]:
    """Fetch index.json and cache it. Returns (ok, message)."""
    try:
        body = _http_get(BOXSHOP_BASE + "index.json")
        data = json.loads(body)
        if not isinstance(data, dict) or "payloads" not in data:
            return False, "catalog malformed"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        INDEX_PATH.write_bytes(body)
        return True, f"{len(data['payloads'])} payloads"
    except urllib.error.URLError as e:
        return False, f"network: {e.reason}"
    except json.JSONDecodeError as e:
        return False, f"parse: {e}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def list_items() -> list[dict]:
    """Return the cached catalog's payload list, or [] if never refreshed."""
    data = _load_json(INDEX_PATH)
    if not data:
        return []
    return data.get("payloads", [])


def get_item(item_id: str) -> Optional[dict]:
    for it in list_items():
        if it.get("id") == item_id:
            return it
    return None


# ---------- install state ----------

def _read_installed() -> dict:
    return _load_json(INSTALLED_PATH) or {}


def _write_installed(state: dict) -> None:
    _save_json(INSTALLED_PATH, state)


def is_installed(item_id: str) -> bool:
    return item_id in _read_installed()


def installed_items() -> dict:
    return _read_installed()


# ---------- install / uninstall ----------

def install(item_id: str) -> tuple[bool, str]:
    """Download + verify + write a payload to its install dir.

    Each manifest file is fetched individually, sha256'd, and only
    written if the digest matches. Partial failures roll back.
    """
    item = get_item(item_id)
    if not item:
        return False, f"unknown payload {item_id!r} (refresh catalog?)"
    payload_type = item.get("type")
    install_dir = _install_dir(payload_type, item_id)
    if install_dir is None:
        return False, f"unsupported type {payload_type!r}"

    path = item.get("path", f"payloads/{item_id}").rstrip("/")
    try:
        manifest_body = _http_get(f"{BOXSHOP_BASE}{path}/manifest.json")
        manifest = json.loads(manifest_body)
    except urllib.error.URLError as e:
        return False, f"manifest fetch: {e.reason}"
    except json.JSONDecodeError as e:
        return False, f"manifest parse: {e}"

    files = manifest.get("files", [])
    if not isinstance(files, list) or not files:
        return False, "manifest has no files"

    # Stage everything in memory + verify before touching disk so a
    # half-install doesn't end up on the device.
    staged: list[tuple[Path, bytes]] = []
    for entry in files:
        src = entry.get("src", "")
        dst = entry.get("dst", "")
        want_sha = (entry.get("sha256") or "").lower()
        if not src or not dst or not want_sha:
            return False, "manifest entry missing src/dst/sha256"
        # Block path-escape via dst — must resolve inside install_dir.
        target = (install_dir / dst).resolve()
        try:
            target.relative_to(install_dir.resolve())
        except ValueError:
            return False, f"manifest dst escapes install dir: {dst!r}"
        try:
            blob = _http_get(f"{BOXSHOP_BASE}{path}/{src}")
        except urllib.error.URLError as e:
            return False, f"file fetch {src!r}: {e.reason}"
        got_sha = _sha256(blob)
        if got_sha != want_sha:
            return False, f"sha256 mismatch on {src!r}"
        staged.append((target, blob))

    install_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    try:
        for target, blob in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            written.append(str(target))
    except OSError as e:
        # Rollback partial writes.
        for p in written:
            try:
                Path(p).unlink()
            except OSError:
                pass
        return False, f"write failed: {e}"

    state = _read_installed()
    state[item_id] = {
        "version": item.get("version", "?"),
        "type": payload_type,
        "files": written,
    }
    _write_installed(state)
    return True, f"installed {len(written)} file(s)"


def uninstall(item_id: str) -> tuple[bool, str]:
    state = _read_installed()
    record = state.get(item_id)
    if not record:
        return False, f"{item_id!r} not installed"

    removed = 0
    for p in record.get("files", []):
        try:
            Path(p).unlink()
            removed += 1
        except OSError:
            pass

    # Best-effort: remove the install dir if it's now empty.
    install_dir = _install_dir(record.get("type", ""), item_id)
    if install_dir and install_dir.is_dir():
        try:
            shutil.rmtree(install_dir)
        except OSError:
            pass

    state.pop(item_id, None)
    _write_installed(state)
    return True, f"removed {removed} file(s)"
