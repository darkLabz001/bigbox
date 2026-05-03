"""Process-wide registry of long-running background tasks.

Several views spin up subprocesses or threads that keep running after
the user backs out of the view (probe sniffer, beacon flood, karma,
wardrive autonomous capture, PMKID sniper, screen recording, …). With
nothing tracking them you have no way to see "what's still going" or
to stop them without re-entering the view that owns them.

This module is a tiny shared registry. Views call :func:`register`
when they spin a task up and :func:`unregister` when it ends. The
``Tasks`` view in the UI lists what's in the registry; the status bar
shows a small "○ N" indicator when N > 0.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Task:
    id: str               # unique key — caller-supplied
    label: str            # human-readable, shown in the Tasks view
    section: str          # short tag for grouping ("Wireless", "Wardrive")
    started_at: float = field(default_factory=time.time)
    stop: Callable[[], None] | None = None  # called when user taps Stop


_lock = threading.Lock()
_tasks: dict[str, Task] = {}


def register(task_id: str, label: str, section: str = "",
             stop: Callable[[], None] | None = None) -> None:
    """Add or overwrite a task in the registry. Idempotent — calling
    twice with the same ``task_id`` updates the entry, not duplicates."""
    with _lock:
        _tasks[task_id] = Task(id=task_id, label=label, section=section,
                               stop=stop)
    try:
        from bigbox import activity
        activity.record(f"started: {label}")
    except Exception:
        pass


def unregister(task_id: str) -> None:
    with _lock:
        task = _tasks.pop(task_id, None)
    if task is not None:
        try:
            from bigbox import activity
            activity.record(f"stopped: {task.label}")
        except Exception:
            pass


def list_tasks() -> list[Task]:
    with _lock:
        return sorted(_tasks.values(), key=lambda t: t.started_at)


def count() -> int:
    with _lock:
        return len(_tasks)


def stop_one(task_id: str) -> bool:
    with _lock:
        task = _tasks.get(task_id)
    if not task:
        return False
    if task.stop:
        try:
            task.stop()
        except Exception as e:
            print(f"[background] stop({task_id}) raised: {e}")
    unregister(task_id)
    return True


def stop_all() -> None:
    with _lock:
        snapshot = list(_tasks.values())
    for task in snapshot:
        if task.stop:
            try:
                task.stop()
            except Exception as e:
                print(f"[background] stop_all({task.id}) raised: {e}")
    with _lock:
        _tasks.clear()
