"""On-device diagnostics — pull recent Python tracebacks out of the
systemd journal so users can see crashes without ssh.

bigbox runs as ``bigbox.service`` under systemd; stdout/stderr land in
the journal. This module shells out to ``journalctl -u bigbox.service``
and parses out blocks that look like Python tracebacks (the standard
``Traceback (most recent call last):`` header through the final
``ExceptionType: message`` line).

Persistent journal isn't enabled by default on this image, so by
default we only see this-boot tracebacks. Still useful for the
"opened the cracker, watch what happens" loop.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


SERVICE_NAME = "bigbox.service"

# A traceback ends at the first non-indented line that looks like an
# exception summary ("ExceptionType: message"). We anchor on that.
_EXC_HEADER = re.compile(r"^\s*Traceback \(most recent call last\):")
_EXC_SUMMARY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Warning|Interrupt)\b")


@dataclass
class Traceback:
    timestamp: str    # journal timestamp, raw
    text: str         # full block including header + final summary line


def _journal_lines(boots: int = 1, max_lines: int = 4000) -> list[str]:
    """Read recent journal lines, with timestamps. ``boots`` counts
    back: 1 = current boot only."""
    cmd = [
        "journalctl",
        "-u", SERVICE_NAME,
        "--no-pager",
        "-o", "short-iso",
        "-n", str(max_lines),
    ]
    if boots == 1:
        cmd += ["-b"]
    elif boots > 1:
        # journalctl wants -b -<n> for the n-th-previous boot;
        # use --since to roughly approximate "last few boots."
        # Simpler: just pull `max_lines` from across all boots.
        pass
    try:
        out = subprocess.check_output(
            cmd, text=True, stderr=subprocess.STDOUT, timeout=10,
        )
    except FileNotFoundError:
        return ["[diagnostics] journalctl not installed"]
    except subprocess.CalledProcessError as e:
        return [f"[diagnostics] journalctl failed: {e.output[:200]}"]
    except Exception as e:
        return [f"[diagnostics] journalctl error: {e}"]
    return out.splitlines()


def recent_tracebacks(boots: int = 1, limit: int = 20) -> list[Traceback]:
    """Parse recent journal output for Python tracebacks. Returns the
    most recent ``limit`` blocks, newest first."""
    lines = _journal_lines(boots=boots)

    # Each journal line is "TIMESTAMP HOSTNAME PROCESS[PID]: message".
    # We only want the message body for traceback detection, but we
    # keep the timestamp for the display.
    parsed: list[tuple[str, str]] = []
    for line in lines:
        # Cheap split: timestamp is the first whitespace-delimited token.
        # If the line doesn't fit, fall back to ("", line).
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[0]:
            ts = parts[0]
            rest = parts[1]
            # Strip "hostname process[pid]: " prefix if present.
            colon = rest.find(": ")
            body = rest[colon + 2:] if colon != -1 else rest
        else:
            ts, body = "", line
        parsed.append((ts, body))

    blocks: list[Traceback] = []
    in_block = False
    block_lines: list[str] = []
    block_ts = ""
    for ts, body in parsed:
        if not in_block and _EXC_HEADER.search(body):
            in_block = True
            block_ts = ts
            block_lines = [body]
            continue
        if in_block:
            block_lines.append(body)
            # The summary line marks the end of a Python traceback.
            if _EXC_SUMMARY.match(body):
                blocks.append(Traceback(
                    timestamp=block_ts,
                    text="\n".join(block_lines),
                ))
                in_block = False
                block_lines = []

    blocks.reverse()  # newest first
    return blocks[:limit]


def render_text(tbs: list[Traceback]) -> str:
    if not tbs:
        return "No tracebacks found in the journal for this boot."
    parts = [f"{len(tbs)} traceback(s):", ""]
    for tb in tbs:
        parts.append(f"--- {tb.timestamp} ---")
        parts.append(tb.text)
        parts.append("")
    return "\n".join(parts)
