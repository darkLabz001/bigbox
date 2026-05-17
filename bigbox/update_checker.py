"""Update Checker — background service to check for GitHub updates."""
from __future__ import annotations

import subprocess
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bigbox.app import App

class UpdateChecker:
    def __init__(self, app: App, interval_seconds: int = 300): # 5 mins default
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
        print(f"[update_checker] started (interval: {self.interval}s)")

    def stop(self) -> None:
        self._stop.set()

    def _check_now(self) -> bool:
        """Runs git fetch and compares local HEAD to remote."""
        try:
            # Retry git fetch with a short timeout: mobile hotspots and weak
            # signal commonly stall fetches, and a single 60s blocking call
            # would freeze the update thread between cycles.
            fetched = False
            for attempt in range(3):
                try:
                    subprocess.run(
                        ["git", "fetch", "origin"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        check=True,
                    )
                    fetched = True
                    break
                except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
                    if self._stop.wait(2 * (attempt + 1)):
                        return False
            if not fetched:
                print("[update_checker] fetch failed after retries")
                return False

            res = subprocess.check_output(
                ["git", "rev-list", "--count", "main..origin/main"],
                text=True, stderr=subprocess.DEVNULL,
                timeout=10,
            ).strip()

            count = int(res)
            if count > 0:
                print(f"[update_checker] found {count} new commits")
            return count > 0
        except Exception as e:
            print(f"[update_checker] check failed: {e}")
            return False

    def _run(self) -> None:
        # Initial wait to let system boot and network stabilize
        print("[update_checker] waiting 10s for network...")
        time.sleep(10)

        while not self._stop.is_set():
            try:
                print("[update_checker] checking for updates...")
                if self._check_now():
                    if not self.update_ready:
                        self.update_ready = True
                        print("[update_checker] update found, triggering toast and notification")
                        self.app.toast("SYSTEM UPDATE AVAILABLE")
                        self.app.play_notification()
                else:
                    print("[update_checker] system up to date")
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
