"""Update Checker — background service to check for GitHub updates."""
from __future__ import annotations

import subprocess
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigbox.app import App

class UpdateChecker:
    def __init__(self, app: App, interval_seconds: int = 3600):
        self.app = app
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.update_ready = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _check_now(self) -> bool:
        """Runs git fetch and compares local HEAD to remote."""
        try:
            # 1. Fetch remote changes without merging. Increased timeout for slow connections.
            subprocess.run(["git", "fetch", "origin"], 
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
            
            # 2. Check if main is behind origin/main
            # rev-list --count main..origin/main returns > 0 if there are new commits
            res = subprocess.check_output(
                ["git", "rev-list", "--count", "main..origin/main"],
                text=True, stderr=subprocess.DEVNULL
            ).strip()
            
            count = int(res)
            return count > 0
        except Exception:
            return False

    def _run(self) -> None:
        # Initial wait to let system boot and network stabilize
        time.sleep(10)

        while not self._stop.is_set():
            try:
                if self._check_now():
                    if not self.update_ready:
                        self.update_ready = True
                        self.app.toast("SYSTEM UPDATE AVAILABLE")
            except Exception:
                # Whole-iteration safety net: a surprise from
                # _check_now (it has its own try/except, but bugs
                # happen) or self.app.toast (App might be tearing
                # down) shouldn't kill the only update poller.
                import traceback
                print("[update_checker] iteration failed:")
                traceback.print_exc()

            # Wait for next interval or stop signal
            for _ in range(self.interval):
                if self._stop.is_set():
                    break
                time.sleep(1)
