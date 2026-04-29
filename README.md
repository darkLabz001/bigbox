# 🏴‍☠️ DaRkb0x (bigbox)
### Tactical Handheld Pentesting Framework for Raspberry Pi
**Ghost Protocol // High-Fidelity Signal Intelligence // Portable Auditing**

DaRkb0x is a self-contained, gamepad-driven tactical firmware designed for the **Raspberry Pi 4** and **Zero 2W**. It transforms your handheld into a powerful signal intelligence and network auditing tool with a high-contrast "Ghost Protocol" interface.

---

## 🛠️ Core Tactical Suite

### 📡 Reconnaissance & Signals
*   **Signal Scraper:** High-fidelity proximity profiler for Wi-Fi and Bluetooth LE. Features real-time RSSI tracking and vendor identification (Apple, Tesla, Samsung, etc.).
*   **FlockSeeker Ultra:** Specialized detection suite for ALPR (Automated License Plate Reader) infrastructure and traffic surveillance nodes.
*   **Camera Interceptor:** Tactical "Dive" mode that hijacks local camera management APs to view internal RTSP/MJPEG streams.
*   **Traffic Cam Browser:** Global uplink to live public traffic and weather feeds from major cities.
*   **Wardriving:** GPS-tagged Wi-Fi and BT sweeps with WiGLE-compatible CSV export.

### 📶 Wireless Auditing
*   **Wifite 2 Interactive:** Fully manual/interactive auditor for WPS, WPA handshakes, and PMKID attacks with a scrollable tactical terminal.
*   **Wi-Fi Multi-Tool:** Integrated scanner for deauth attacks and handshake captures.
*   **Evil Twin:** Rogue AP suite with captive portal for credential harvesting.

### 💬 Social & Communication
*   **Tactical Messenger:** Threaded SMS client supporting **Textbelt** (free daily texts) and **Carrier Gateways** (unlimited texts via Email).
*   **Tactical Mail:** Full-featured IMAP/SMTP email client optimized for handheld screens.
*   **Global Chat:** In-device encrypted chat client via `darksec.uk`.
*   **Local BBS:** LAN-based message board and dead-drop.

### 📺 Media & Entertainment
*   **Internet TV:** Dual-mode HLS/mpv player with a curated list of international news and lifestyle channels.
*   **Media Player:** Local movie and video browser with hardware-optimized playback.

---

## 📸 Screenshots

<img width="605" height="354" alt="abot" src="https://github.com/user-attachments/assets/d94c161a-0141-4c97-8f2d-330e985a178c" />  <img width="600" height="357" alt="2" src="https://github.com/user-attachments/assets/dc62d6d7-599b-4e17-8d82-be5b071be5fc" />   <img width="610" height="371" alt="3" src="https://github.com/user-attachments/assets/99b6b361-9902-4360-92de-c90c79a52400" />    <img width="601" height="369" alt="4" src="https://github.com/user-attachments/assets/2f2180fa-61cf-4265-8221-6117480b0c27" />   <img width="602" height="359" alt="7" src="https://github.com/user-attachments/assets/1c71d676-e153-4609-a8b6-df8b384e33af" />   <img width="610" height="366" alt="6" src="https://github.com/user-attachments/assets/0fe44bb9-a894-48ec-bf9e-4b5abafaaced" />   
<img width="1866" height="792" alt="8" src="https://github.com/user-attachments/assets/f4e693c1-a860-43a3-9b97-79a7abae0bad" />   <img width="1086" height="1448" alt="box" src="https://github.com/user-attachments/assets/55613f75-78dd-4a1d-96a4-428a60744252" />

---

## 🎮 Interface & Controls

DaRkb0x is designed for fast, one-handed operation using the GamePi43 controller or any external USB/BLE keyboard.

| Command | GamePi43 Button | Keyboard |
| :--- | :--- | :--- |
| **Navigate** | D-Pad | Arrows / WASD |
| **Initiate / Select** | Button A | Enter / Space / Z |
| **Back / Cancel** | Button B | Escape / X |
| **Secondary Action** | Button X | C |
| **Context Menu** | Button Y | V |
| **Left Shoulder** | LL Button | Q / L |
| **Right Shoulder** | RR Button | E / R |
| **System Menu** | Start | Return |
| **Tool Config** | Select | Backspace / Tab |

---

## 📦 Hardware Requirements

*   **Processor:** Raspberry Pi 4 (2GB+) or Pi Zero 2W.
*   **Display:** [Waveshare GamePi43](https://www.waveshare.com/wiki/GamePi43) (4.3" 800×480 IPS).
*   **Wi-Fi:** Onboard + Optional **Alfa AWUS036ACS** (RTL8821AU) for monitor mode.
*   **Bluetooth:** Onboard + Optional **TP-Link USB Adapter** (hci0 priority).
*   **GPS:** USB NMEA dongle (Quectel LC86L recommended) for Wardriving.

---

## 🚀 Installation

### 1. Fast Deployment (SD Card Prep)
Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. Then, run our prep script on your Linux machine:
```bash
sudo BIGBOX_HOSTNAME=darkbox ./scripts/sdcard-prepare.sh /dev/sdX
```

### 2. Manual Installation
If you already have Pi OS running:
```bash
git clone https://github.com/darkLabz001/bigbox.git
cd bigbox
sudo ./scripts/install.sh
sudo systemctl enable --now bigbox
```

---

## 🔄 System Updates (OTA)
DaRkb0x features a high-fidelity **OTA Update System**. Go to **Settings > Check for Updates** to pull the latest tactical payloads, dependencies, and bug fixes. The update process includes real-time Discord notifications for repository changes.

---

## 🚀 More to Come...
*   **Mesh Intercom:** PTT voice notes over local P2P mesh.
*   **Ghost Intel:** Anonymous target monitoring (Twitter/Reddit front-ends).
*   **Digital Graffiti:** GPS-locked hidden notes for field coordination.
*   **SDR Integration:** Basic signal sniffing for 433MHz/Sub-GHz.

---

## ⚖️ Legal
*For educational and authorized security testing purposes only. Usage on networks without explicit permission is strictly prohibited.*
