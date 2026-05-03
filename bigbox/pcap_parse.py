"""Inline parser for hcxdumptool .pcapng output.

Shells out to ``hcxpcapngtool`` (ships with hcxtools, already a
dependency since hcxdumptool is) to convert a captured .pcapng into
the hashcat-22000 hash format, then parses each line into something
the UI can render — (kind, BSSID, ESSID).

Without this, the PMKID Sniper just drops a binary .pcapng next to
the others and you have no idea which APs you actually got. With it,
the result screen lists every captured handshake/PMKID with its
network name.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Capture:
    kind: str        # "PMKID" or "EAPOL"
    bssid: str       # "aa:bb:cc:dd:ee:ff"
    essid: str       # decoded SSID, "?" if undecodable


_RE_HEX_MAC = re.compile(r"^[0-9a-fA-F]{12}$")


def _format_bssid(hex12: str) -> str:
    if not _RE_HEX_MAC.match(hex12):
        return hex12
    h = hex12.lower()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def _decode_essid(hex_str: str) -> str:
    try:
        b = bytes.fromhex(hex_str)
        # SSIDs are usually UTF-8 / ASCII; replace bytes that aren't.
        return b.decode("utf-8", errors="replace") or "?"
    except Exception:
        return "?"


def parse_pcapng(pcapng_path: Path,
                 timeout_sec: int = 30) -> list[Capture]:
    """Run hcxpcapngtool and parse its 22000-format hash output into
    a list of Capture rows. Returns an empty list if the tool is
    missing, the file is empty, or no captures matched."""
    if not pcapng_path.is_file():
        return []
    out_file = Path(f"/tmp/bigbox-pmkid-{pcapng_path.stem}.22000")
    try:
        subprocess.run(
            ["hcxpcapngtool", "-o", str(out_file), str(pcapng_path)],
            check=False, timeout=timeout_sec,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"[pcap_parse] hcxpcapngtool failed: {e}")
        return []
    if not out_file.is_file():
        return []

    out: list[Capture] = []
    seen: set[tuple[str, str]] = set()
    try:
        for line in out_file.read_text().splitlines():
            line = line.strip()
            if not line.startswith("WPA*"):
                continue
            # Hashcat 22000 format:
            #   WPA*<TYPE>*<MIC|PMKID>*<MAC_AP>*<MAC_CLIENT>*
            #       <ESSID_HEX>*<NONCE_AP>*<EAPOL>*<MESSAGEPAIR>
            parts = line.split("*")
            if len(parts) < 6:
                continue
            type_code = parts[1]
            kind = "PMKID" if type_code == "01" else "EAPOL"
            bssid = _format_bssid(parts[3])
            essid = _decode_essid(parts[5])
            key = (kind, bssid)
            if key in seen:
                continue
            seen.add(key)
            out.append(Capture(kind=kind, bssid=bssid, essid=essid))
    except Exception as e:
        print(f"[pcap_parse] read failed: {e}")
        return []
    return out
