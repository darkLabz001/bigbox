"""Network — interfaces, routes, DNS."""
from __future__ import annotations

from bigbox.runner import run_capture
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _interfaces(ctx: SectionContext) -> None:
    ctx.show_result("interfaces", run_capture(["ip", "-c=never", "addr"]))


def _routes(ctx: SectionContext) -> None:
    ctx.show_result("routes", run_capture(["ip", "-c=never", "route"]))


def _ping_gateway(ctx: SectionContext) -> None:
    out = run_capture(["sh", "-c", "ip route | awk '/default/ {print $3; exit}' | xargs -r ping -c 4 -W 1"])
    ctx.show_result("ping default gateway", out or "[no default route found]\n")


def _resolv(ctx: SectionContext) -> None:
    ctx.show_result("DNS config", run_capture(["sh", "-c", "cat /etc/resolv.conf"]))


def _public_ip(ctx: SectionContext) -> None:
    out = run_capture(["sh", "-c", "curl -s --max-time 4 https://api.ipify.org || echo offline"])
    ctx.show_result("public IP", out)


def _anonsurf(ctx: SectionContext) -> None:
    ctx.show_anonsurf()


def _bettercap(ctx: SectionContext) -> None:
    ctx.show_bettercap()


def _data_sniper(ctx: SectionContext) -> None:
    ctx.show_data_sniper()


def _random_mac(ctx: SectionContext) -> None:
    # Down interface, randomize, up interface
    cmd = "sudo ip link set wlan0 down && sudo macchanger -r wlan0 && sudo ip link set wlan0 up"
    out = run_capture(["sh", "-c", cmd])
    ctx.show_result("random MAC", out)


def build() -> Section:
    return Section(
        title="Network",
        icon="[~]",
        icon_img=load_icon("network"),
        background_img=load_background("network"),
        actions=[
            Action("Interfaces (ip addr)", _interfaces),
            Action("Routes (ip route)", _routes),
            Action("Ping default gateway", _ping_gateway),
            Action("DNS config", _resolv),
            Action("Public IP", _public_ip),
            Action("Anon Surf (Stealth)", _anonsurf, "Route all traffic via Tor"),
            Action("Random MAC (wlan0)", _random_mac, "Randomize hardware address"),
            Action("Bettercap Dashboard", _bettercap, "Real-time network monitoring"),
            Action("Data Sniper", _data_sniper, "Extract credentials and POST data"),
        ],
    )
