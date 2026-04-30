"""Toolbox — Deep system maintenance and optional tool installation."""
from __future__ import annotations

from pathlib import Path
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _fix_deps(ctx: SectionContext) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "fix-deps.sh"
    ctx.show_update("Fixing Dependencies", [str(script)])


def _install_osint(ctx: SectionContext) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "install-osint.sh"
    ctx.show_update("Installing OSINT Suite", [str(script)])


def _install_ragnar(ctx: SectionContext) -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "install_ragnar.sh"
    ctx.show_update("Installing Ragnar", [str(script)])


def build() -> Section:
    return Section(
        title="Toolbox",
        icon="[T]",
        icon_img=load_icon("settings"), # Use settings icon for now
        background_img=load_background("settings"),
        actions=[
            Action("Verify Core Tools", _fix_deps, "Ensure all base dependencies are present"),
            Action("Install OSINT Suite", _install_osint, "Install Sherlock, theHarvester, PhoneInfoga"),
            Action("Install Ragnar", _install_ragnar, "Advanced Headless WiFi Framework"),
        ],
    )
