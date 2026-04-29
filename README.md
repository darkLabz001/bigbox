## BIGBOX


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

## OTA Updates

The tool supports Over-The-Air updates via GitHub. 

1.  **Initialize your remote** (if not done by the prep script):
    ```bash
    git remote add origin https://github.com/darklabz001/bigbox.git
    git branch -M main
    ```
2.  **Go to Settings > Check for updates (OTA)** in the UI. 
    The tool will pull the latest changes from `main`, install new dependencies, and restart the `bigbox` service automatically.

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

## OTA updates

The Pi can pull updates from `https://github.com/darkLabz001/bigbox` automatically.

### One-time conversion (run once on the Pi)

If `/opt/bigbox` was deployed via `sdcard-prepare.sh` or rsync, it's not yet a git checkout. Convert it:

```bash
sudo /opt/bigbox/scripts/git-init-pi.sh
```

This backs up `/opt/bigbox` to `/opt/bigbox.pre-git-<timestamp>`, copies your current `config/buttons.toml` to `/etc/bigbox/buttons.toml` (so OTA never overwrites your pin map), then makes `/opt/bigbox` a real clone of upstream.

### Triggering updates

| How | Command / step |
|-----|------|
| **From the device UI** | Settings → "Check for updates (OTA)" |
| One-shot from SSH | `sudo systemctl start bigbox-update.service` |
| Hourly auto-update | `sudo systemctl enable --now bigbox-update.timer` |
| What it logs | `journalctl -u bigbox-update -e` |

`scripts/update.sh` is what does the work: `git fetch`, hard-reset to `origin/main`, reinstall pip deps if `requirements.txt` changed, reinstall the systemd unit if `bigbox.service` changed, then `systemctl restart bigbox.service`.

### Things that survive an OTA update

- Your `.venv/` (gitignored).
- `/etc/bigbox/buttons.toml` (lives outside `/opt/bigbox`; loaded with priority by `bigbox.input.config`).
- Any backup directories named `/opt/bigbox.pre-*`.

Anything else inside `/opt/bigbox` will be reset to the upstream tip on update.

## Status

Early. UI engine, input router, install script, stubs and OTA mechanism are in. Real tools to follow.


SCREENSHOTS

<img width="605" height="354" alt="abot" src="https://github.com/user-attachments/assets/d94c161a-0141-4c97-8f2d-330e985a178c" />  <img width="600" height="357" alt="2" src="https://github.com/user-attachments/assets/dc62d6d7-599b-4e17-8d82-be5b071be5fc" />   <img width="610" height="371" alt="3" src="https://github.com/user-attachments/assets/99b6b361-9902-4360-92de-c90c79a52400" />    <img width="601" height="369" alt="4" src="https://github.com/user-attachments/assets/2f2180fa-61cf-4265-8221-6117480b0c27" />   <img width="602" height="359" alt="7" src="https://github.com/user-attachments/assets/1c71d676-e153-4609-a8b6-df8b384e33af" />   <img width="610" height="366" alt="6" src="https://github.com/user-attachments/assets/0fe44bb9-a894-48ec-bf9e-4b5abafaaced" />   
<img width="1866" height="792" alt="8" src="https://github.com/user-attachments/assets/f4e693c1-a860-43a3-9b97-79a7abae0bad" />   <img width="1086" height="1448" alt="box" src="https://github.com/user-attachments/assets/55613f75-78dd-4a1d-96a4-428a60744252" />










## Legal

For use only on systems and networks you own or have written permission to test.
