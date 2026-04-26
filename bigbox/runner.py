"""Wraps subprocess so sections can stream tool output into a ResultView.

Runs the command in a daemon thread, line-buffered, and pushes each line back
to a callback (typically ResultView.append).
"""
from __future__ import annotations

import shlex
import subprocess
import threading
from typing import Callable


def run_streaming(argv: list[str], on_line: Callable[[str], None]) -> threading.Thread:
    """Launches argv and streams stdout+stderr to on_line. Returns the thread."""
    def _worker() -> None:
        on_line(f"$ {' '.join(shlex.quote(a) for a in argv)}\n")
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            on_line(f"[error] command not found: {argv[0]}\n")
            on_line("[hint] is the package installed? check scripts/install.sh\n")
            return
        except PermissionError as e:
            on_line(f"[error] {e}\n")
            return
        assert proc.stdout is not None
        for line in proc.stdout:
            on_line(line)
        proc.wait()
        on_line(f"\n[exit {proc.returncode}]\n")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


def run_capture(argv: list[str], timeout: float = 30.0) -> str:
    """One-shot capture for tools that finish quickly. Returns combined output."""
    try:
        out = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return out.stdout or ""
    except FileNotFoundError:
        return f"[error] command not found: {argv[0]}\n"
    except subprocess.TimeoutExpired:
        return f"[error] {argv[0]} timed out after {timeout}s\n"
    except PermissionError as e:
        return f"[error] {e}\n"
