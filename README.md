# wifi-box v2

Portable WiFi security testing box for Raspberry Pi with a 1.44" SPI LCD and
GPIO controls. A native attack engine drives `airodump-ng`/`aireplay-ng` (WPA
handshake capture) and `reaver`/`bully` (WPS pixie-dust) directly — no wifite2,
no terminal-output scraping. Captures handshakes, cracks PSKs on demand (on-box
or on your server), and uploads results over Tailscale.

Built for **Raspberry Pi OS Bookworm Lite (64-bit)**.

## Features

- Text-based menu UI (battery + Tailscale status bar)
- Native WPA handshake capture + WPS pixie-dust (optional PMKID), 2.4 & 5 GHz
- Multi-BSSID target select (include) and multi-BSSID exclude
- Structured live attack status screen (targets, handshakes, cracked, failed)
- Manual cracking only — on-box (`aircrack-ng`, rockyou) or offload to the server
- Uses external USB WiFi for attacks; internal card (wl0) left alone
- Connect to a known (cracked) network, then connect to Tailscale and SCP results
- Auto-start via systemd

## Quick install (fresh Pi)

```bash
cd ~/C && sudo bash install.sh
```

The script (9 steps, shows progress + spinners):
1. Checks platform (Pi model, OS version)
2. Enables SPI + I2C in `/boot/config.txt`
3. Installs all system packages via apt (incl. `hcxdumptool` + rockyou wordlist)
4. Names internal WiFi `wl0` via systemd link file
5. Grants passwordless sudo for attack tools
6. Creates systemd auto-start service
7. Guides Tailscale setup
8. Sets up project directory + cracked.json
9. Verifies all tools and modules

### After install

```bash
# Authenticate Tailscale (do once)
sudo tailscale up

# Restart to apply SPI/wl0 naming
sudo reboot
```

## Usage

### Controls

The Waveshare 1.44" LCD HAT has a joystick and 3 buttons:

| Control | Action |
|---------|--------|
| **Joystick Up/Down** | Scroll through menu items |
| **Joystick Press** | Select / activate / toggle |
| **Joystick Left** | Back (same as KEY1) |
| **KEY1** (top button) | Back to previous menu |
| **KEY2** (middle button) | Toggle option |
| **KEY3** (bottom button) | Stop current attack |

### Screen layout

```
╭─ ATTACK ────────╮   ← Every screen: header (title + TS indicator)
│>> CoffeeShop 23s │   ← Current target + elapsed time
│█████████░░░░   32%│   ← Progress bar
│HS CoffeeShop     │   ← Latest event (compact)
│G:1 H:2 F:0       │   ← Counters: G=cracked H=handshakes F=failed
│─────────────────│
│+ CoffeeShop=pass │   ← Recent results (4 most recent)
│H HomeWiFi        │
│> OfficeNet       │
│                  │
│███████░░░ 3.9V   │   ← Battery bar: white>50% orange 30-50% red<30%
╰─────────────────╯
```

**Attack status glyphs:**
`+` cracked with password · `H` handshake captured · `>` in progress ·
`C` cracking · `-` failed · `~` skipped

### Walkthrough

#### First boot

1. Power on → splash screen → **MAIN** menu.
2. Check **SysCheck** — verify attack tools, interfaces, Tailscale.
3. Plug in external USB WiFi adapter. Internal WiFi stays as `wl0`.

#### Attack a network

**Method A — Quick Attack (fire and forget):**
1. **Quick Attack** → pick a preset (e.g. PIXIE Rush, WPA Grab)
2. The box scans for 30s, then attacks all targets found.
3. Live status screen shows progress. Press KEY3 to cancel.

**Method B — Scan & Attack (pick targets):**
1. **Scan & Attack** → scans for networks.
2. Press to toggle a `+` on each target you want (select as many as you like);
   the top **Attack (N)** row launches once you've chosen.
3. Choose "Run: Current Config" or a preset → attacks only the selected BSSIDs.

Use **Config → Exclude SSIDs** to mark BSSIDs to skip during a Quick Attack scan.

#### Cracking (manual)

Captured handshakes are stored but never cracked automatically. Open **Crack**,
pick a capture, then choose:
- **On-box (rockyou)** — runs `aircrack-ng` against the local wordlist (slow on a Pi).
- **On server (upload)** — SCPs the `.cap`/`.22000` to your server to crack there.

WPS pixie-dust recovers the PSK directly, so those show up already cracked.

#### Viewing results

**Cracked** shows all networks with captured passwords. Press on an entry to
connect to that network. Entries without a password show as `(no-psk)`.

#### Connecting to a cracked network

**Connect** lists networks you have passwords for. Select one:
1. Tries internal WiFi (`wl0`) first via `nmcli`.
2. Falls back to external adapter if internal fails.
3. Shows ✓ Connected → brings up Tailscale → shows server reachability.

#### Uploading results

**Upload** SCPs cracked results to your server over Tailscale:
1. Checks Tailscale is up, pings server.
2. Only sends entries you haven't uploaded before.
3. Shows ✓ done with count.

#### Configuring attacks

**Config** has 5 submenus:

| Submenu | What toggles |
|---------|-------------|
| **Attack Modes** | WPA, WPS Pixie, WPS Tool (reaver/bully), Deauth, Ignore Locks, PMKID |
| **Timing** | Scan Time, WPS Timeout, WPA Timeout, Deauth Sec, Num Deauths |
| **Target Filters** | Band (Both/2.4/5), Min Signal, Max Targets, Ignore Cracked |
| **Interface** | Random MAC |
| **Exclude SSIDs** | Scan → toggle BSSIDs to exclude from attacks |

Toggle style: press to cycle. Booleans show a **green circle** (ON) or **red circle** (OFF).
Cycle values show the current option. **Save as Preset** stores your current config.

#### Tailscale

1. `sudo tailscale up` once on the Pi (auth via browser/phone).
2. Every screen shows **TS** in the header: green = up + server pingable, red = down.
3. **Connect** and **Upload** depend on Tailscale for network reachability.

### Presets

| Preset | Attacks | Use case |
|--------|---------|----------|
| **PIXIE Rush** | WPS pixie (30s) | Fast WPS pixie dust |
| **WPA Grab** | WPA handshake | Handshake capture |
| **WPS + WPA** | WPS pixie (60s) + WPA | Try pixie, fall back to a handshake |
| **PIXIE Q60** | WPS pixie (60s) | WPS pixie with more time |
| **Survey Only** | none | Scan only, no attacks |

Presets set which attacks run; capture never auto-cracks. Cracking is a separate,
manual step (see below).

### Auto-start

The box boots straight into the menu (systemd `wifibox` service). To stop:

```bash
sudo systemctl stop wifibox
```

To disable auto-start:

```bash
sudo systemctl disable wifibox
```

### Logs

```bash
tail -f ~/C/wifibox.log        # application log
journalctl -u wifibox -f       # systemd log
```

### Creating an SD card image

Once everything works, run `sudo bash finalize.sh` to clean up, then clone the SD
card on another machine:

```bash
sudo dd if=/dev/sdX of=wifibox.img bs=4M status=progress
sudo pishrink.sh -z wifibox.img    #  → wifibox.img.gz
```

Burn to new SDs with `dd`.

## Project layout

```
main.py            entry point, settings restore on boot
config.py          pins, presets, defaults, server, RaspberryPi HW class
cracked_store.py   read/write with corruption recovery, migration, settings
hardware/          display (LCD), gpio (shared lgpio), buttons, battery (INA219)
ui/                theme (6 colors), menus, screens (splash, attack status)
attack/            interface, scanner, model, wpa, wps, pmkid, orchestrator, crack, tools
network/           connect (nmcli→internal→external), tailscale, upload (incremental SCP)
install.sh         fresh-Pi setup (step counter + spinner)
finalize.sh        clean up before SD card imaging
LCD_1in44.py       Waveshare 1.44" ST7735S LCD driver (kept)
INA219.py          INA219 I2C battery sensor driver (kept)
AGENTS.md          dev context for future agents
```

## Upload target

- Server: `100.124.251.39` (Tailscale)
- User: `sun`
- SCP path: `/home/sun/handshake/`

Adjust in `config.py` (`UPLOAD_SERVER`, `UPLOAD_USER`, `HANDSHAKE_REMOTE_DIR`).

## Notes

- Attack commands use `sudo` (passwordless, configured by install.sh).
- WiFi scan tries `iw` → `iwlist` → `nmcli` fallbacks.
- GPIO falls back across `/dev/gpiochip4|0|1`; runs headless (logged) if unavailable.
- LCD and buttons share a single lgpio chip handle — no `gpiozero` needed.
- Monitor mode: `airmon-ng` with fallback to `iw dev ... set type monitor`.
- USB adapters use in-tree `rtl8xxxu` driver (no DKMS needed on 6.x kernels).
- Runtime data in `~/.local/share/wifi-box/` (migrated from project root on first run).
- Settings (theme, exclusions) persist across reboots via `settings.json`.
- Corrupted `cracked.json` self-heals by backing up and resetting.
