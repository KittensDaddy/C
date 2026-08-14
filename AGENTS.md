# wifi-box v2 — Agent Context

Pi: `sun@cybercat` running Bookworm, has Waveshare 1.44" LCD HAT + external USB WiFi.

## Current state

Native attack engine (no wifite2): `airodump-ng`/`aireplay-ng` (WPA handshake) and
`reaver`/`bully` (WPS pixie-dust) driven directly, reading structured output — no
stdout scraping. **Push last commit → Pi pulls → `sudo systemctl restart wifibox` to test.**

### What works (verified on dev machine)
- All modules compile + import; full app runs headless without GPIO/LCD
- WPA handshake capture (airodump pcap + aircrack handshake check), optional PMKID
- WPS pixie (`reaver -K 1`, bully fallback): PIN vs PSK distinguished, PSK recovered
  from a pixie PIN (`reaver -p <pin>`), and again from the Cracked detail view
- Multi-BSSID target include/exclude, band + signal filters, presets
- cracked_store: corrupt-tolerant JSON + upload state tracking + `update_cracked()`
- install.sh: step counters, spinner, tailscale repo
- LCD RGB565 encoding is pure-PIL (no numpy)

### To test after pull on Pi
```bash
systemctl status wifibox          # should show LCD splash + menu
iwconfig                          # check external USB wifi shows up
tail -f ~/C/wifibox.log           # any runtime errors
sudo wifite --help | head -3      # (this SHOULD fail — wifite2 is gone)
```

### Dependency notes
- **Attack tools:** aircrack-ng, aireplay-ng, airodump-ng, reaver, bully, hcxdumptool
  (+ hcxpcapngtool for PMKID→.22000). On-box crack = aircrack-ng + rockyou; the Pi
  has no hashcat (server cracks with its own).
- **UI/HW:** python3, python3-pil, lgpio, spidev, smbus2 (INA219). No numpy.
- Not installed on purpose: wifite2, hashcat, macchanger, dnsutils, pip/setuptools,
  DKMS RTL8192EU driver (in-tree `rtl8xxxu` handles monitor mode on 6.x kernels).

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
ui/theme          6 themes, shadowed text, circle, progress_bar, marquee
ui/screens         splash, AttackStatus, ProgressView, CommandPreview, status_bar
ui/menus           all menus (main, config, scan, presets, connect, upload, cracked detail)
attack/interface   IF detection, monitor mode on/off (airmon-ng→iw fallback)
attack/scanner     iw→iwlist→nmcli scan fallback
attack/orchestrator  target resolution → monitor on → per-target WPS/WPA/PMKID
attack/wpa         airodump+aireplay handshake capture (pcap, no stdout scrape)
attack/wps         reaver pixie (bully fallback); PIN→PSK recovery
attack/pmkid       optional hcxdumptool → hcxpcapngtool → .22000
attack/crack       manual on-box (aircrack/rockyou) or server upload; never auto
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
- Attack modes default enables everything (WPS+WPA+pixie). Users may want to toggle
  selectively; a pixie PIN without a recovered PSK is not connectable (use the
  Cracked detail view → Recover PSK).
- The main loop blocks during long-running attack status — button events during
  attacks only go to the stop-monitor thread. Fine for v1.
