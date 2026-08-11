# wifi-box v2

Portable WiFi security testing box for Raspberry Pi with a 1.44" SPI LCD and
GPIO controls. Runs [wifite2](https://github.com/kimocoder/wifite2), captures
handshakes, cracks PSKs, and uploads results to your server over Tailscale.

Built for **Raspberry Pi OS Bookworm Lite (64-bit)**.

## Features

- Nested text-based menu UI (themeable, battery + Tailscale status bar)
- WPA/WPA2/WPA3/WPS/Pixie/PMKID attacks via wifite2 presets & custom config
- Structured live attack status screen (targets, handshakes, cracked, failed)
- Smart error fallbacks (interface detection, scan methods, monitor mode, connect, upload)
- Uses external USB WiFi for attacks; internal card (wl0) left alone
- Connect to a known (cracked) network, then connect to Tailscale and SCP results
- Auto-start via systemd

## Quick install (fresh Pi)

```bash
# on the Pi
sudo bash install.sh
```

The script:
1. Enables SPI + I2C
2. Installs all packages (no pip venv needed)
3. Installs wifite2 (kimocoder) into `/opt/wifite2`
4. Names the internal WiFi `wl0`
5. (Optional) builds the RTL8192EU USB driver via dkms
6. Grants passwordless sudo for the attack tools
7. Installs a `wifibox` systemd service (auto-start)
8. Guides Tailscale setup
9. Verifies the install and offers to reboot

### Manual dependency install

```bash
sudo apt install -y wifite aircrack-ng iw wireless-tools tailscale nmcli \
  python3-lgpio python3-pil python3-spidev python3-smbus2 python3-numpy
```

## Usage

Power on → splash → main menu:

| Menu | Action |
|------|--------|
| **Scan & Attack** | scan → pick target → current config or preset → run |
| **Quick Attack** | run a saved preset immediately |
| **Config** | attack modes, timing, target filters, interface, exclude SSIDs, save preset |
| **Cracked** | view cracked.json; press an entry to connect to that network |
| **Connect** | connect to a cracked network (internal first, external fallback) |
| **Upload** | SCP cracked.json to `100.124.251.39:/home/sun/handshake` over Tailscale |
| **Resume** | resume an interrupted wifite2 session |
| **SysCheck** | verify tools / interfaces / tailscale |

Controls: joystick up/down to scroll, press to select, key1 = back,
key2 = toggle (or stealth).

### Battery indicator

Bottom bar: **white** >50% · **orange** 30–50% · **red** <30%.

## Project layout

```
main.py            entry point
config.py          pins, presets, defaults, server
cracked_store.py   cracked.json read/write (corruption-tolerant)
hardware/          display (LCD), buttons (lgpio), battery (INA219)
ui/                theme, menus, screens
wifite/            interface, scanner, attacker, output parser
network/           connect, tailscale, upload
install.sh         fresh-Pi setup
LCD_1in44.py       Waveshare 1.44" LCD driver (kept)
INA219.py          INA219 I2C driver (kept)
```

## Upload target

- Server: `100.124.251.39` (Tailscale)
- User: `sun`
- SCP path: `/home/sun/handshake/`

Adjust in `config.py` (`UPLOAD_SERVER`, `UPLOAD_USER`, `HANDSHAKE_REMOTE_DIR`).

## Notes

- Attack commands run via `sudo` (configured passwordless by install.sh).
- WiFi scan tries `iw` → `iwlist` → `nmcli` fallbacks.
- GPIO falls back across `/dev/gpiochip4|0|1`; runs headless (logged) if no GPIO.
- If the LCD can't init, the app still runs headless and logs to `wifibox.log`.
