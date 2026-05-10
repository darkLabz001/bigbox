"""Waveform — SDR (Software Defined Radio) tools."""
from __future__ import annotations

from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _adsb(ctx: SectionContext) -> None:
    ctx.show_adsb()


def _pager(ctx: SectionContext) -> None:
    ctx.show_pager()


def _rtlamr(ctx: SectionContext) -> None:
    ctx.run_streaming("AMR · 900MHz", ["rtlamr", "-format", "json"])


def _rtl_433(ctx: SectionContext) -> None:
    ctx.run_streaming("433MHz · ISM", ["rtl_433", "-f", "433.92M", "-F", "json"])


def build() -> Section:
    return Section(
        title="Waveform",
        icon="[~]",
        icon_img=load_icon("recon"), # Fallback to recon icon
        background_img=load_background("recon"),
        actions=[
            Action("ADS-B Tracker", _adsb, "live aircraft tracking (1090MHz)"),
            Action("Pager Sniffer", _pager, "POCSAG/FLEX decoding"),
            Action("Smart Meter (AMR)", _rtlamr, "scan 900MHz utility meters"),
            Action("433MHz Scanner", _rtl_433, "rtl_433 json output"),
        ],
    )
