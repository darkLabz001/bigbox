# bigbox

A pentesting handheld firmware for Raspberry Pi 4 + Waveshare GamePi43.

## What it is

A self-contained UI that boots straight onto the GamePi43's 4.3" screen and gives you fast, gamepad-driven access to recon, network, wireless and Bluetooth tooling. No window manager, no desktop — just the carousel.

## Layout

```
LEFT shoulder   <-- [Recon] [Network] [Wireless] [Bluetooth] [Settings] [About] -->   RIGHT shoulder
                          ^
                          |
                  D-pad UP/DOWN scrolls
                  the list inside the page
                  A activates, B goes back
```

## Hardware

- Raspberry Pi 4 (2GB+ recommended)
- Waveshare GamePi43 (4.3" 800×480 IPS, DPI parallel-RGB, GPIO buttons, 3.5mm audio)
- Optional: USB Wi-Fi adapter that supports monitor mode (the Pi's onboard radio can do it but external is more reliable for handshakes)
- Optional: USB Bluetooth dongle

## Putting it on an SD card

You don't need to boot the Pi first. Workflow:

1. **Flash Raspberry Pi OS Lite (64-bit)** to the SD card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Set a user/password in the Imager's advanced options. Don't eject yet.
2. **Plug the same card into your Linux dev box** (the card will re-mount as `boot` and `rootfs`). Note the device — `lsblk` will show something like `/dev/sdb` or `/dev/mmcblk0`.
3. **Run the prep script** from this repo:

   ```bash
   sudo ./scripts/sdcard-prepare.sh /dev/sdX           # bare minimum
   # or with options:
   sudo BIGBOX_SSH=1 \
        BIGBOX_WIFI_SSID="my-wifi" \
        BIGBOX_WIFI_PSK="hunter2" \
        BIGBOX_WIFI_COUNTRY=US \
        BIGBOX_HOSTNAME=bigbox \
        ./scripts/sdcard-prepare.sh /dev/sdX
   ```

   It refuses non-removable disks and asks you to type `yes` before touching anything.

4. **Eject, put the card in the Pi, power on.** First boot: expands the rootfs, waits for network, runs `install.sh` (apt deps + venv + systemd), then reboots into bigbox. Watch progress with `journalctl -fu bigbox` once SSH is up, or read `/var/log/bigbox-firstrun.log`.

> The Waveshare GamePi43 display driver is **not** auto-installed — display revisions vary too much. Once the card boots, run Waveshare's `LCD-show` per the [GamePi43 wiki](https://www.waveshare.com/wiki/GamePi43) for your unit.

### Already booted into Pi OS?

If your Pi is up and you just want to install bigbox over SSH:

```bash
git clone <this repo> ~/bigbox
sudo ~/bigbox/scripts/install.sh
sudo systemctl enable --now bigbox
```

Run on a dev machine (no GamePi43 hardware) for UI development:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
BIGBOX_DEV=1 python -m bigbox
```

In dev mode the keyboard stands in for the GamePi43:

| Key | Button |
|-----|--------|
| Arrow keys | D-pad |
| Z / X | A / B |
| A / S | X / Y |
| Q / W | LL / RR shoulders |
| H | HK (Hotkey) |
| Enter | Start |
| Right Shift | Select |

## Adding a section

Drop a file in `bigbox/sections/` that subclasses `Section` and lists `Action` items. The carousel picks it up at startup.

## Adding a tool to a section

Each `Action` is a label + callable. The callable can be a shell-out via `bigbox.runner.run` (streams output into a result view) or a Python function that returns a result string.

## Layout on disk

```
bigbox/             package
  app.py            main loop
  events.py         button event constants
  theme.py          colors/fonts
  runner.py         subprocess wrapper
  input/            GPIO + keyboard input drivers
  ui/               carousel, scroll list, widgets
  sections/         one file per page (recon, network, ...)
config/buttons.toml GPIO -> event mapping (edit if pins differ)
scripts/install.sh  apt deps + boot config + systemd
scripts/bigbox.service
```

## Status

Early. UI engine, input router, install script and stubs are in. Real tools to follow.

## Legal

For use only on systems and networks you own or have written permission to test.
