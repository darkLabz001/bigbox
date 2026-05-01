"""Disk-space awareness — status-bar HUD + auto-rotation of loot dirs.

The Pi 4 SD card hosts the OS, the venv, captures, recordings,
handshakes, wardrive CSVs, and the saved scan JSONs. None of those
have explicit retention; over a few weeks of use the card silently
fills and writes start failing in surprising places (cracker can't
append to loot/cracked.txt; ffmpeg's screen recording errors out).

:func:`free_mb` is cheap (statvfs is microseconds) but we still cache
for 5 s so it doesn't run per render frame. :func:`auto_rotate_sweep`
runs in a daemon thread on app startup and prunes oldest files in a
small set of well-known directories whenever free space drops below
the soft threshold.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path


# Soft + hard thresholds. Below soft we start trimming; below hard we
# trim more aggressively (drop to 30% of the per-dir budget).
SOFT_MB = 1024
HARD_MB = 500

# Directories the sweeper is allowed to prune from. Order matters
# only for tie-breaking — the sweep just trims oldest by mtime in
# each, never crossing dirs.
ROTATABLE_DIRS = (
    Path("media/captures"),
    Path("media/captures/recordings"),
    Path("loot/scans"),
    Path("loot/handshakes"),
    Path("loot/wardrive"),
)

# Per-dir cap in MB; sweep starts trimming when the dir exceeds this
# AND overall free space is below SOFT_MB.
PER_DIR_CAP_MB = {
    Path("media/captures"): 1024,
    Path("loot/scans"): 256,
    Path("loot/handshakes"): 1024,
    Path("loot/wardrive"): 1024,
}

_TARGET_FS = "/"  # statvfs target — root covers /opt/bigbox + media/loot

_cache_lock = threading.Lock()
_cached_free_mb: int = 0
_cache_ts: float = 0.0
_CACHE_SECONDS = 5.0


def free_mb(target: str = _TARGET_FS) -> int:
    """Free megabytes on the partition holding ``target``. Cached 5s."""
    global _cached_free_mb, _cache_ts
    now = time.monotonic()
    with _cache_lock:
        if now - _cache_ts < _CACHE_SECONDS and _cached_free_mb:
            return _cached_free_mb
    try:
        st = os.statvfs(target)
        free = (st.f_bavail * st.f_frsize) // (1024 * 1024)
    except OSError:
        free = 0
    with _cache_lock:
        _cached_free_mb = int(free)
        _cache_ts = now
    return _cached_free_mb


def dir_size_mb(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total // (1024 * 1024)


def _prune_oldest(path: Path, target_mb: int) -> int:
    """Delete oldest files in ``path`` until the dir is at or below
    ``target_mb``. Returns megabytes freed."""
    if not path.is_dir():
        return 0
    files: list[tuple[float, int, Path]] = []
    for p in path.iterdir():
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        files.append((st.st_mtime, st.st_size, p))
    files.sort(key=lambda t: t[0])  # oldest first
    current = sum(f[1] for f in files) // (1024 * 1024)
    freed_bytes = 0
    for mtime, size, p in files:
        if current <= target_mb:
            break
        try:
            p.unlink()
            freed_bytes += size
            current -= size // (1024 * 1024)
        except OSError as e:
            print(f"[disk] could not delete {p}: {e}")
    return freed_bytes // (1024 * 1024)


def sweep_once() -> None:
    """One pass of the rotation policy. No-op if free space is fine."""
    fmb = free_mb()
    if fmb >= SOFT_MB:
        return
    aggressive = fmb < HARD_MB
    for d in ROTATABLE_DIRS:
        cap = PER_DIR_CAP_MB.get(d, 256)
        target = int(cap * (0.3 if aggressive else 0.6))
        size = dir_size_mb(d)
        if size > target:
            freed = _prune_oldest(d, target)
            if freed:
                print(f"[disk] rotated {d}: -{freed} MB "
                      f"(was {size} MB → cap {target} MB; free was {fmb} MB)")
    # Force the cache to refresh next call so the HUD reflects the
    # newly-freed space immediately.
    global _cache_ts
    _cache_ts = 0.0


def start_sweeper(interval_sec: float = 300.0) -> threading.Thread:
    """Spawn a daemon thread that calls :func:`sweep_once` every
    ``interval_sec`` (default 5 min). Returns the Thread."""
    def _loop():
        # Settle period — don't sweep instantly on boot.
        time.sleep(60)
        while True:
            try:
                sweep_once()
            except Exception as e:
                print(f"[disk] sweep error: {e}")
            time.sleep(interval_sec)

    t = threading.Thread(target=_loop, daemon=True, name="disk-sweep")
    t.start()
    return t
