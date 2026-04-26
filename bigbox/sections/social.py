"""Social / Media — OSINT and social-media recon.

Most actions here are stubs that point to a CLI workflow on a TTY, since
the things you'd want to do (sherlock, theHarvester, phoneinfoga) all
need text input we can't yet capture from gamepad buttons. Once an
on-device keyboard widget exists, these become first-class actions.
"""
from __future__ import annotations

from bigbox.runner import run_capture
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _username_hint(ctx: SectionContext) -> None:
    ctx.show_result(
        "username search",
        "Find a username across hundreds of social sites.\n\n"
        "Drop to a TTY (Ctrl-Alt-F2) and run:\n"
        "    sudo apt-get install -y sherlock\n"
        "    sherlock <username>\n\n"
        "Output goes to ./<username>.txt with all matches.\n",
    )


def _email_hint(ctx: SectionContext) -> None:
    ctx.show_result(
        "email harvester",
        "Pull emails / subdomains for a target domain from public sources.\n\n"
        "Drop to a TTY (Ctrl-Alt-F2) and run:\n"
        "    sudo apt-get install -y theharvester\n"
        "    theHarvester -d <domain> -b all\n\n"
        "Sources: bing, duckduckgo, github, hunter, etc.\n",
    )


def _phone_hint(ctx: SectionContext) -> None:
    ctx.show_result(
        "phone OSINT",
        "Reverse-lookup a phone number (carrier, region, format).\n\n"
        "Drop to a TTY (Ctrl-Alt-F2) and run:\n"
        "    pipx install phoneinfoga\n"
        "    phoneinfoga scan -n <number>\n",
    )


def _wayback(ctx: SectionContext) -> None:
    """One-shot, no input needed: shows the Wayback Machine availability for
    bigbox's own GitHub repo as a smoke test of the API."""
    target = "https://github.com/darkLabz001/bigbox"
    out = run_capture([
        "sh", "-c",
        f"curl -s --max-time 6 'https://archive.org/wayback/available?url={target}'"
        " | python3 -m json.tool 2>/dev/null"
        " || echo offline",
    ])
    ctx.show_result(f"wayback · {target}", out)


def _whois_repo(ctx: SectionContext) -> None:
    out = run_capture(["sh", "-c", "whois github.com 2>&1 | head -40 || echo 'whois not installed'"])
    ctx.show_result("whois · github.com", out)


def build() -> Section:
    return Section(
        title="Social",
        icon="[s]",
        icon_img=load_icon("media"),
        background_img=load_background("media"),
        actions=[
            Action("Username search (sherlock)", _username_hint, "OSINT"),
            Action("Email harvester (theHarvester)", _email_hint, "OSINT"),
            Action("Phone OSINT (phoneinfoga)", _phone_hint, "OSINT"),
            Action("Wayback availability check", _wayback),
            Action("WHOIS · github.com", _whois_repo),
        ],
    )
