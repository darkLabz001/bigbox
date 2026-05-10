"""SDR (Software Defined Radio) Process Management.

Provides a unified interface for starting and stopping various RTL-SDR
based tools like dump1090, rtl_433, and multimon-ng.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Optional


class SDRProcess:
    def __init__(self, name: str, cmd: list[str]) -> None:
        self.name = name
        self.cmd = cmd
        self.proc: Optional[subprocess.Popen] = None

    def is_installed(self) -> bool:
        return shutil.which(self.cmd[0]) is not None

    def start(self) -> bool:
        if not self.is_installed():
            return False
        if self.proc and self.proc.poll() is None:
            return True
        try:
            self.proc = subprocess.Popen(
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
            )
            return True
        except Exception:
            return False

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def read_line(self) -> Optional[str]:
        if not self.proc or not self.proc.stdout:
            return None
        try:
            return self.proc.stdout.readline().strip()
        except Exception:
            return None


def get_adsb() -> SDRProcess:
    # --net-sbs-port 30003 is the standard port for BaseStation/SBS data
    return SDRProcess("ADS-B", ["dump1090", "--quiet", "--net"])


def get_pager() -> SDRProcess:
    # rtl_fm pipes to multimon-ng for POCSAG/FLEX decoding
    # This requires shell=True or a more complex pipe handling, 
    # but for simplicity we'll wrap it in a script or handle it here.
    cmd = ["bash", "-c", "rtl_fm -f 152.0M -s 22050 -g 40 - | multimon-ng -t raw -a POCSAG512 -a POCSAG1200 -a POCSAG2400 -f alpha -"]
    return SDRProcess("Pager", cmd)


def get_subghz() -> SDRProcess:
    return SDRProcess("rtl_433", ["rtl_433", "-F", "json", "-M", "level"])
