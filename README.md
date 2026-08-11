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
cd ~/C && sudo bash install.sh
```

The script (10 steps, shows progress + spinners):
1. Checks platform (Pi model, OS version)
2. Enables SPI + I2C in `/boot/config.txt`
3. Installs all system packages via apt
4. Clones wifite2 (pinned commit) and runs `python3 setup.py install`
5. Names internal WiFi `wl0` via systemd link file
6. Grants passwordless sudo for attack tools
7. Creates systemd auto-start service
8. Guides Tailscale setup
9. Sets up project directory + cracked.json
10. Verifies all tools and modules
11. Optionally creates an SD card image

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
| **KEY2** (middle button) | Toggle option / Stealth mode |
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
2. Check **SysCheck** — verify wifite, interfaces, Tailscale.
3. Plug in external USB WiFi adapter. Internal WiFi stays as `wl0`.

#### Attack a network

**Method A — Quick Attack (fire and forget):**
1. **Quick Attack** → pick a preset (e.g. PIXIE Rush, WPA Grab)
2. The box scans for 30s, then attacks all targets found.
3. Live status screen shows progress. Press KEY3 to cancel.

**Method B — Scan & Attack (pick one target):**
1. **Scan & Attack** → scans for networks.
2. Select a target from the list → choose "Run: Current Config" or a preset.
3. Attack starts against only that target. No pillage (`-p`) — cleaner.

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
| **Attack Modes** | WPS, Pixie Dust, Null PIN, WPS Tool (reaver/bully), Ignore Locks, WPA, PMKID, Deauth, No Deauth, WPA3, Force SAE, WEP |
| **Timing** | Scan Time, WPS/WPA/Deauth/PMKID timeouts, Num Deauths, Loop mode |
| **Target Filters** | Band selection, Clients Only, Min Signal, Max Targets, Ignore Cracked, Skip Crack |
| **Interface** | Random MAC, Dual Interface, Kill Conflicts, Daemon, Hcxdump |
| **Exclude SSIDs** | Scan → toggle SSIDs to exclude from attacks |

Toggle style: press to cycle. Booleans show a **green circle** (ON) or **red circle** (OFF).
Cycle values show the current option. **Save as Preset** stores your current config.

#### Tailscale

1. `sudo tailscale up` once on the Pi (auth via browser/phone).
2. Every screen shows **TS** in the header: green = up + server pingable, red = down.
3. **Connect** and **Upload** depend on Tailscale for network reachability.

### Presets

| Preset | Command | Use case |
|--------|---------|----------|
| **PIXIE Rush** | `--wps-only --pixie --wps-time 30` | Fast WPS pixie dust |
| **WPA Grab** | `--no-wps --no-pmkid --nodeauths` | Passive WPA handshake capture |
| **PMKID Hunter** | `--no-wps --pmkid` | Roaming PMKID capture |
| **Full Power** | `-ab` | All attacks, 2.4GHz + 5GHz |
| **PIXIE Q60** | `--wps-only --pixie --wps-time 60` | WPS pixie with more time |
| **Survey Only** | `--skip-crack --nodeauths` | Scan only, no attacks or cracks |
| **WPA3 Focus** | `--wpa3` | WPA3-only attacks |

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
wifite/            interface, scanner, attacker, typed output parser
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
