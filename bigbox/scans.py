"""Persisted scan results.

ARP scan and the probe sniffer both gather lists that used to vanish
when the view exited. This module dumps each completed scan to
``loot/scans/<type>_<ISO>.json`` so a user can review them later from
the Loot → Scan History view, or push them out via webhook.

Schema is forward-compatible: each scan is a single JSON object with a
``type`` field discriminator. Unknown fields are tolerated when loading.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path


SCANS_DIR = Path("loot/scans")


@dataclass
class ScanRecord:
    type: str                              # "arp" | "probe"
    started_iso: str = ""
    ended_iso: str = ""
    iface: str = ""
    target: str = ""                       # ARP target range / probe doesn't use
    devices: list[dict] = field(default_factory=list)
    probes: list[dict] = field(default_factory=list)
    total_frames: int = 0                  # probe-only metric

    def filename(self) -> str:
        # ISO-ish but filename-safe.
        ts = self.started_iso.replace(":", "").replace("-", "").replace("T", "_")
        if "." in ts:
            ts = ts.split(".", 1)[0]
        if not ts:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return f"{self.type}_{ts}.json"

    def summary(self) -> str:
        if self.type == "arp":
            return (f"{self.iface or '?'} :: {self.target or 'localnet'}  "
                    f":: {len(self.devices)} hosts")
        if self.type == "probe":
            macs = len({p.get('mac', '') for p in self.probes})
            ssids = len({p.get('ssid', '') for p in self.probes})
            return (f"{self.iface or '?'} :: {macs} devices, "
                    f"{ssids} SSIDs, {self.total_frames} frames")
        return self.type


def save(record: ScanRecord) -> Path | None:
    if not record.ended_iso:
        record.ended_iso = datetime.utcnow().isoformat(timespec="seconds")
    if not record.started_iso:
        record.started_iso = record.ended_iso
    try:
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        path = SCANS_DIR / record.filename()
        with path.open("w", encoding="utf-8") as f:
            json.dump(asdict(record), f, indent=2)
        return path
    except Exception as e:
        print(f"[scans] save failed: {e}")
        return None


def list_saved() -> list[Path]:
    if not SCANS_DIR.is_dir():
        return []
    files = [p for p in SCANS_DIR.iterdir() if p.is_file() and p.suffix == ".json"]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def load(path: Path | str) -> ScanRecord | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[scans] load {path}: {e}")
        return None
    # Drop unknown fields rather than raising — older/newer schemas mix.
    known = ScanRecord.__dataclass_fields__.keys()
    clean = {k: v for k, v in data.items() if k in known}
    try:
        return ScanRecord(**clean)
    except Exception as e:
        print(f"[scans] parse {path}: {e}")
        return None


def render_text(record: ScanRecord) -> str:
    """Plain-text rendering for ResultView display + webhook share."""
    lines = []
    lines.append(f"TYPE     {record.type}")
    lines.append(f"STARTED  {record.started_iso}")
    lines.append(f"ENDED    {record.ended_iso}")
    lines.append(f"IFACE    {record.iface or '?'}")
    if record.target:
        lines.append(f"TARGET   {record.target}")
    lines.append(f"SUMMARY  {record.summary()}")
    lines.append("")
    if record.type == "arp" and record.devices:
        lines.append("HOSTS")
        for d in record.devices:
            klass = d.get("device_class") or ""
            klass = f" [{klass}]" if klass else ""
            lines.append(f"  {d.get('ip',''):16} {d.get('mac',''):17}  "
                         f"{d.get('vendor','')[:32]}{klass}")
    elif record.type == "probe" and record.probes:
        lines.append("PROBES (mac, vendor, ssid, count)")
        for p in record.probes:
            klass = p.get("device_class") or ""
            klass = f" [{klass}]" if klass else ""
            vendor = (p.get("vendor") or "")[:14]
            lines.append(f"  {p.get('mac',''):17} {vendor:<15} "
                         f"{p.get('ssid','')[:36]:<36} ×{p.get('count',1)}{klass}")
    return "\n".join(lines)
