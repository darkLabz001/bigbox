"""Settings — system controls."""
from __future__ import annotations

from bigbox.runner import run_capture
from bigbox.sections._icons import load as load_icon, load_background
from bigbox.ui import Action, Section, SectionContext


def _vol_up(ctx: SectionContext) -> None:
    out = run_capture(["amixer", "-c", "1", "set", "PCM", "5%+"])
    ctx.show_result("volume +", out)


def _vol_down(ctx: SectionContext) -> None:
    out = run_capture(["amixer", "-c", "1", "set", "PCM", "5%-"])
    ctx.show_result("volume -", out)


def _vol_mute(ctx: SectionContext) -> None:
    out = run_capture(["amixer", "-c", "1", "set", "PCM", "toggle"])
    ctx.show_result("mute toggle", out)


def _reboot(ctx: SectionContext) -> None:
    # bigbox runs as root under bigbox.service, so call systemctl
    # directly. Avoids the sudo round-trip and won't hang on a
    # missing sudoers entry.
    ctx.run_streaming("reboot", ["systemctl", "reboot"])


def _poweroff(ctx: SectionContext) -> None:
    ctx.run_streaming("poweroff", ["systemctl", "poweroff"])


def _view_loot(ctx: SectionContext) -> None:
    import os
    fname = "loot/flock_intel.txt"
    if not os.path.exists(fname):
        ctx.show_result("Flock Loot", "No loot captured yet.\n\nRun FlockSeeker to gather intel.")
        return
        
    try:
        with open(fname, "r") as f:
            content = f.read()
            if not content.strip():
                content = "Loot file is empty."
            ctx.show_result("Flock Loot", content)
    except Exception as e:
        ctx.show_result("Error", f"Could not read loot: {e}")


def _wifi_connect(ctx: SectionContext) -> None:
    ctx.show_wifi()


def _terminal(ctx: SectionContext) -> None:
    ctx.show_terminal()


def _theme_manager(ctx: SectionContext) -> None:
    ctx.show_theme_manager()


def _tailscale(ctx: SectionContext) -> None:
    ctx.show_tailscale()


def _web_access(ctx: SectionContext) -> None:
    ctx.show_web_access()


def _diagnostics(ctx: SectionContext) -> None:
    ctx.show_diagnostics()


def _background_tasks(ctx: SectionContext) -> None:
    ctx.show_background_tasks()


def _update(ctx: SectionContext) -> None:
    # Always resolve the script via the package layout, never via cwd.
    from pathlib import Path
    script = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
    ctx.show_update("OTA update", [str(script)])


def _toolbox_menu(ctx: SectionContext) -> None:
    from pathlib import Path
    script_dir = Path(__file__).resolve().parents[2] / "scripts"

    def fix_deps():
        ctx.show_update("Fixing Dependencies", [str(script_dir / "fix-deps.sh")])

    def install_osint():
        ctx.show_update("Installing OSINT Suite", [str(script_dir / "install-osint.sh")])

    def install_ragnar():
        ctx.show_update("Installing Ragnar", [str(script_dir / "install_ragnar.sh")])

    def setup_webhook():
        from bigbox import webhooks
        current = webhooks.load_webhook_url() or ""
        def save_cb(val):
            if val is not None:
                webhooks.save_webhook_url(val)
                ctx.toast("Webhook URL saved")
            ctx.go_back()
        ctx.get_input("Webhook URL", save_cb, current)

    actions = [
        ("Verify Core Tools", fix_deps),
        ("Install OSINT Suite", install_osint),
        ("Install Ragnar", install_ragnar),
        ("Webhook Setup", setup_webhook),
    ]
    ctx.show_menu("Toolbox", actions)


def _network_menu(ctx: SectionContext) -> None:
    ctx.show_menu("Network", [
        ("Web UI Access (QR)", lambda: ctx.show_web_access()),
        ("Connect to Wi-Fi",   lambda: ctx.show_wifi()),
        ("Tailscale VPN",      lambda: ctx.show_tailscale()),
    ])


def _diagnostics_menu(ctx: SectionContext) -> None:
    ctx.show_menu("Diagnostics", [
        ("Running Tasks",   lambda: ctx.show_background_tasks()),
        ("Recent Crashes",  lambda: ctx.show_diagnostics()),
        ("View Flock Loot", lambda: _view_loot(ctx)),
    ])


def _power_menu(ctx: SectionContext) -> None:
    ctx.show_menu("Power & Audio", [
        ("Volume up",   lambda: _vol_up(ctx)),
        ("Volume down", lambda: _vol_down(ctx)),
        ("Mute toggle", lambda: _vol_mute(ctx)),
        ("Reboot",      lambda: _reboot(ctx)),
        ("Power off",   lambda: _poweroff(ctx)),
    ])


def _system_menu(ctx: SectionContext) -> None:
    ctx.show_menu("System", [
        ("Bash Terminal",          lambda: ctx.show_terminal()),
        ("Theme Manager",          lambda: ctx.show_theme_manager()),
        ("Toolbox",                lambda: _toolbox_menu(ctx)),
        ("Check for updates (OTA)", lambda: _update(ctx)),
    ])


def build() -> Section:
    return Section(
        title="Settings",
        icon="[=]",
        icon_img=load_icon("settings"),
        background_img=load_background("settings"),
        actions=[
            # Top-level: most-used at the top, submenus for the rest.
            Action("Web UI Access", _web_access, "Scan a QR with your phone — auto login"),
            Action("Network",       _network_menu, "Wi-Fi, Tailscale, Web UI access"),
            Action("Diagnostics",   _diagnostics_menu, "Running tasks, crash log, loot"),
            Action("System",        _system_menu, "Terminal, themes, toolbox, OTA"),
            Action("Power & Audio", _power_menu, "Volume, reboot, power off"),
        ],
    )
