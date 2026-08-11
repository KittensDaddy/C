# wifi-box v2 — Agent Context

Pi: `sun@cybercat` running Bookworm, has Waveshare 1.44" LCD HAT + RTL8192EU USB WiFi.

## Current state (2aced99)

**Push last commit → Pi pulls → `sudo systemctl restart wifibox` to test.**

### What works (verified on dev machine)
- All modules compile + import
- Command building: presets and all config toggles generate correct wifite2 argv
- Output parser: tested against real wifite2 patterns (scan, attack, handshake, crack, fail)
- cracked_store: corrupt-tolerant JSON + upload state tracking
- install.sh: step counters, spinner, tailscale repo, kernel-headers auto-detect

### What's broken on the Pi
1. **LCD white screen** — `config.RaspberryPi` class was missing (LCD_1in44.py inherits from it). Added in 46e650c. Pi needs: `sudo apt install python3-gpiozero && git pull && sudo systemctl restart wifibox`
2. **wifite2 not installed** — old install script failed at `pip install -e .`. Pi needs: `cd ~/C && git pull && sudo bash install.sh` (now uses `setup.py install`)
3. **Attack status screen shows nothing useful** — old parser matched wrong patterns. Fixed in 2aced99 with parser based on actual wifite2 source.
4. **RTL8192EU dkms hangs** — old driver doesn't build on Bookworm 6.x. Skip it. In-tree `rtl8xxxu` handles monitor mode natively.

### To test after pull on Pi
```bash
systemctl status wifibox          # should show LCD splash + menu
iwconfig                          # check external USB wifi shows up
sudo wifite --help | head -3      # wifite installed?
tail -f ~/C/wifibox.log           # any runtime errors
```

### File map
```
main.py            entry — splash → buttons → menu
config.py          pins, presets, HW defaults, RaspberryPi class (for LCD_1in44)
cracked_store.py   cracked.json read/write + upload_state.json tracking
install.sh         fresh-Pi setup
finalize.sh        cleanup for SD card imaging
hardware/display   LCD wrapper (headless fallback)
hardware/buttons   lgpio GPIO (chip fallback 4→0→1)
hardware/battery   INA219: white(>50%)/orange(30-50%)/red(<30%)
ui/theme          6 themes, shadowed text, circle, progress_bar
ui/screens         splash, AttackStatus, ProgressView, status_bar
ui/menus           all menus (main, config, scan, presets, connect, upload, ...)
wifite/interface   IF detection, monitor mode on/off (airmon-ng→iw fallback)
wifite/scanner     iw→iwlist→nmcli scan fallback
wifite/attacker    build wifite cmd from config, run, parse output
wifite/output      parser for real wifite2 stdout (kimocoder)
network/connect    nmcli→wpa_supplicant (internal→external), dhcpcd→dhclient→nmcli
network/tailscale  status, up, ping server
network/upload     incremental SCP to 100.124.251.39:/home/sun/handshake/
LCD_1in44.py       1.44" LCD driver (inherits config.RaspberryPi)
INA219.py          INA219 I2C battery sensor driver
```

### GPIO pin map (BCM)
```
KEY_UP=6  KEY_DOWN=19  KEY_LEFT=5  KEY_RIGHT=26  KEY_PRESS=13
KEY1=21   KEY2=20      KEY3=16
LCD_RST=27  LCD_DC=25  LCD_BL=24
SPI: SCLK=11 MOSI=10 CS=8 (CE0)
```

### Known issues
- `_config_flags` produces `-p 30` which means pillage=30s then auto-attack all targets. Fine for presets but the "Scan & Attack" flow lets user pick a single target first. For single-target attack, should NOT pass `-p` (or pass `-p 0`). Only Quick Attack / presets should use `-p`.
- Attack modes: default config enables everything (WPS+WPA+pixie). Users may want to toggle selectively.
- The main loop blocks during long-running attack status — button events during attacks only go to the stop-monitor thread. Fine for v1.
