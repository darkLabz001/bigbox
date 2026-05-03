"""Process-wide rolling activity log — feed for the status-bar ticker.

Tools call :func:`record(msg)` whenever something noteworthy happens
(background task starts/stops, webhook fires, capture saved, scan
persisted, …) and the StatusBar pulls the most-recent entries via
:func:`recent` to show a one-line "what just happened" summary.

Pure in-memory, bounded. The Diagnostics view already covers
durable history; this is just for the live UI.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


_MAX = 50


@dataclass
class Event:
    ts: float
    message: str


_lock = threading.Lock()
_log: deque[Event] = deque(maxlen=_MAX)


def record(message: str) -> None:
    """Append a one-line event to the activity log. Safe from any
    thread; never raises."""
    if not message:
        return
    try:
        with _lock:
            _log.append(Event(ts=time.time(), message=message[:120]))
    except Exception:
        pass


def recent(n: int = 10) -> list[Event]:
    with _lock:
        return list(_log)[-n:]


def latest() -> Event | None:
    with _lock:
        return _log[-1] if _log else None


def clear() -> None:
    with _lock:
        _log.clear()
