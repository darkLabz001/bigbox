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
    ctx.run_streaming("reboot", ["sudo", "reboot"])


def _poweroff(ctx: SectionContext) -> None:
    ctx.run_streaming("poweroff", ["sudo", "poweroff"])


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


def _regen_web_token(ctx: SectionContext) -> None:
    from bigbox.web import auth as web_auth
    new = web_auth.regenerate_token()
    ctx.show_result("Web Token Regenerated",
                    f"New token:\n\n  {new}\n\n"
                    "All existing browser sessions are now invalid.")


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

    def regen_token():
        _regen_web_token(ctx)

    actions = [
        ("Verify Core Tools", fix_deps),
        ("Install OSINT Suite", install_osint),
        ("Install Ragnar", install_ragnar),
        ("Webhook Setup", setup_webhook),
        ("Regenerate Web Token", regen_token),
    ]
    ctx.show_menu("Toolbox", actions)


def build() -> Section:
    return Section(
        title="Settings",
        icon="[=]",
        icon_img=load_icon("settings"),
        background_img=load_background("settings"),
        actions=[
            Action("Web UI Access", _web_access, "Scan a QR with your phone — auto login"),
            Action("Connect to Wi-Fi", _wifi_connect, "scan, select, save a network"),
            Action("Tailscale VPN", _tailscale, "secure access to your private network"),
            Action("Theme Manager", _theme_manager, "install and manage themes"),
            Action("Bash Terminal", _terminal, "full root shell with OSK"),
            Action("Toolbox", _toolbox_menu, "System maintenance and tool installation"),
            Action("Check for updates (OTA)", _update),
            Action("View Flock Loot", _view_loot, "intel gathered from FlockSeeker"),
            Action("Volume up", _vol_up),
            Action("Volume down", _vol_down),
            Action("Mute toggle", _vol_mute),
            Action("Reboot", _reboot),
            Action("Power off", _poweroff),
        ],
    )
