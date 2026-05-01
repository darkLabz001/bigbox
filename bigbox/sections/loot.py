"""Loot — Secure vault and captured data management."""
from __future__ import annotations
import os
from pathlib import Path
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _vault(ctx: SectionContext) -> None:
    ctx.show_vault()


def _scan_history(ctx: SectionContext) -> None:
    ctx.show_scan_history()


def _raw_loot(ctx: SectionContext) -> None:
    # Use the existing ResultView-based loot viewer from settings.py
    fname = "loot/flock_intel.txt"
    if not os.path.exists(fname):
        ctx.show_result("Raw Intel", "No intel captured yet.")
        return
    with open(fname, "r") as f:
        ctx.show_result("Raw Intel", f.read())


def _view_file(ctx: SectionContext, path: Path) -> None:
    try:
        with open(path, "r", errors="replace") as f:
            content = f.read()
            ctx.show_result(path.name, content)
    except Exception as e:
        ctx.show_result("Error", f"Could not read {path.name}: {e}")


def _view_wifi_loot(ctx: SectionContext) -> None:
    items = []
    base = Path("loot")
    for subdir in ["wifi", "captive"]:
        d = base / subdir
        if d.exists():
            for f in sorted(d.iterdir(), reverse=True):
                if f.is_file() and not f.name.startswith("."):
                    size = f.stat().st_size / 1024
                    desc = f"{subdir.upper()} · {size:.1f} KB"
                    # Create a closure for the handler
                    def make_handler(p):
                        return lambda c: _view_file(c, p)
                    
                    items.append(Action(f.name, make_handler(f), desc))
    
    if not items:
        ctx.show_result("WiFi Captures", "No captures found in loot/wifi/ or loot/captive/.")
        return
    
    # We don't have a direct 'show_list' in SectionContext, but we can 
    # use show_result with a custom view if we had one.
    # For now, let's just show a summary in a ResultView.
    summary = "\n".join([f"{a.label} ({a.description})" for a in items])
    ctx.show_result("WiFi Captures", "Listing contents:\n\n" + summary + "\n\n(Use terminal to inspect binary .pcap files)")


def build() -> Section:
    return Section(
        title="Loot",
        icon="[L]",
        icon_img=load_icon("recon"), # Fallback to recon icon for now
        background_img=load_background("recon"),
        actions=[
            Action("Secure Vault", _vault, "Password-protected encrypted storage"),
            Action("Scan History", _scan_history, "Saved ARP and probe-request scans"),
            Action("Raw Intel", _raw_loot, "View unencrypted session logs"),
            Action("WiFi Captures", _view_wifi_loot, "Handshakes, PMKIDs, and Captive logs"),
        ],
    )
