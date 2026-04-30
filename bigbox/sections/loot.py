"""Loot — Secure vault and captured data management."""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _vault(ctx: SectionContext) -> None:
    ctx.show_vault()


def _raw_loot(ctx: SectionContext) -> None:
    # Use the existing ResultView-based loot viewer from settings.py
    # (We could move the logic here, but keeping it simple for now)
    import os
    from bigbox.runner import run_capture
    fname = "loot/flock_intel.txt"
    if not os.path.exists(fname):
        ctx.show_result("Raw Intel", "No intel captured yet.")
        return
    with open(fname, "r") as f:
        ctx.show_result("Raw Intel", f.read())


def build() -> Section:
    return Section(
        title="Loot",
        icon="[L]",
        icon_img=load_icon("recon"), # Fallback to recon icon for now
        background_img=load_background("recon"),
        actions=[
            Action("Secure Vault", _vault, "Password-protected encrypted storage"),
            Action("Raw Intel", _raw_loot, "View unencrypted session logs"),
        ],
    )
