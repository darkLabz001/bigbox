"""Tiny QR adapter — wraps segno so the rest of the app doesn't have
to import it directly. Returns a 2D bool grid; rendering is the
caller's job (in our case, blitting black/white rects with pygame).

If segno isn't installed (older OTA images, fresh dev clone without
``pip install -r requirements.txt``), :func:`make_matrix` returns
``None`` so callers can fall back to plain text.
"""
from __future__ import annotations


def make_matrix(text: str, error: str = "M") -> list[list[bool]] | None:
    """Return a 2D bool grid for ``text``.

    True = dark module, False = light. Quiet zone is *not* included —
    callers should pad the bounding box themselves so the QR scans
    reliably (4 modules per side is the spec).
    """
    if not text:
        return None
    try:
        import segno  # type: ignore
    except Exception:
        return None
    try:
        qr = segno.make(text, error=error, micro=False)
    except Exception:
        return None
    grid = []
    for row in qr.matrix:
        grid.append([bool(c) for c in row])
    return grid


def lan_ipv4() -> str | None:
    """Best-effort: pick the first non-loopback IPv4 from `ip`. Returns
    None if no usable address is found. Tailscale (CGNAT 100.64/10) is
    deliberately treated as separate from LAN — see :func:`tailscale_ipv4`."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["ip", "-o", "-4", "addr", "show"], text=True, timeout=2,
        )
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        iface = parts[1]
        cidr = parts[3]
        ip = cidr.split("/", 1)[0]
        if iface == "lo" or iface == "tailscale0":
            continue
        if ip.startswith("127."):
            continue
        return ip
    return None


def tailscale_ipv4() -> str | None:
    import subprocess
    try:
        out = subprocess.check_output(
            ["tailscale", "ip", "-4"], text=True, timeout=2,
        )
    except Exception:
        return None
    ip = out.strip().splitlines()[0] if out.strip() else ""
    return ip or None
