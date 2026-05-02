"""Bundle loot + captures into a single tar.gz and ship via webhook.

Captures, screenshots, scan JSONs, handshakes, wardrive CSVs, cracked
passwords, and Flock intel all live in a sprawl of paths. After a
session you usually want one button that bundles everything and ships
it to your phone (or wherever the webhook is wired). This module is
the bundling half; webhook posting is delegated to :mod:`bigbox.webhooks`.
"""
from __future__ import annotations

import tarfile
from datetime import datetime
from pathlib import Path
from typing import Iterable


BUNDLE_SOURCES: tuple[Path, ...] = (
    Path("loot"),
    Path("media/captures"),
)


def _has_files(p: Path) -> bool:
    if not p.is_dir():
        return False
    try:
        for entry in p.rglob("*"):
            if entry.is_file():
                return True
    except OSError:
        return False
    return False


def bundle(out_dir: Path = Path("/tmp"),
           sources: Iterable[Path] = BUNDLE_SOURCES) -> Path | None:
    """tar.gz every non-empty directory in ``sources``. Returns the
    bundle path or None if nothing existed to include."""
    real = [p for p in sources if _has_files(p)]
    if not real:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"bigbox-loot-{ts}.tar.gz"
    try:
        with tarfile.open(out, "w:gz") as tar:
            for src in real:
                # arcname=src.name keeps the top-level structure simple
                # (loot/, media/captures/ → captures/) without leaking
                # absolute paths from the device.
                arcname = "captures" if src == Path("media/captures") else src.name
                tar.add(src, arcname=arcname)
    except OSError as e:
        print(f"[loot_export] bundle failed: {e}")
        return None
    return out


def bundle_size_mb(path: Path) -> int:
    try:
        return path.stat().st_size // (1024 * 1024)
    except OSError:
        return 0
