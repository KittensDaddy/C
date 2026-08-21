# wifi-box v2 — Agent Context

Pi: `sun@cybercat` / `sun@192.168.1.83` (DietPi/Bookworm), Waveshare 1.44" LCD HAT
+ external USB WiFi (**RTL8822BU** / `rtw88_8822bu`). SSH password often `1120`.
Keep **main** and **ipcam** in sync when pushing.

## Current state

Native attack engine (no wifite2): `airodump-ng`/`aireplay-ng` (WPA handshake) and
`reaver`/`bully` + **OneShot** (WPS pixie) driven directly — structured logs, no
stdout scraping of a wrapper. **Push → Pi `git pull` / reset → `sudo systemctl restart wifibox`.**

### PIXIE Rush (bake-off) — latest WPS path

Per AP (pixie-dust only; **no online PIN grind**, **no vendor-PIN probes**):

1. **reaver `-K 1`** (monitor) — soft-bail no beacon (~15s) / no assoc (~35s) /
   no M3 after M1 (~18s); ~12s in-session hold after PIN for PSK before kill
2. **OneShot `-K`** (managed on **external** iface only, ≤25s) — skip if soft-bail
3. **GETPSK** (`reaver -p`) — 8s cool + 45s first try; pin-without-psk queued for
   **post-list retry** (45s cool, signal-sorted, 120s)
4. Abort GETPSK early on repeated deauth

Preset: `band=Both`, `min_signal=Off`, `ignore_cracked=True`, `scan=15`, `wps_time=60`.
Target order: **2.4 GHz first**, then known-WPS, then signal.

### Experiment logs (tune from these)

```bash
grep -aE 'wps-method|wps-ap|wps-metric|run-metric|targets:|run-start' ~/C/wifibox.log
```

- `wps-method` — each attempt (`reaver_pixie` / `oneshot_pixie` / `getpsk` / `getpsk_retry`)
- `wps-ap` — per-AP winner / pin_from / psk_from
- `run-metric` — session totals (`win_reaver`, `win_oneshot`, `psk_retry`, …)
- `targets: scan=N after_filter=M` — empty list diagnosis

### What works (verified on dev machine)
- All modules compile + import; full app runs headless without GPIO/LCD
- WPA handshake capture (airodump pcap + aircrack handshake check), optional PMKID
- WPS pixie + OneShot fallback; PIN vs PSK; Cracked detail → Recover PSK
- Post-Rush GETPSK retry for pin-without-psk
- Multi-BSSID include/exclude, band + signal filters, presets
- cracked_store: corrupt-tolerant JSON + upload state + MAC merge + `update_cracked()`
- Connect: wpa_supplicant primary; restore home Wi‑Fi on failure
- USB: `ensure_external(recover=False)` by default (fast sysfs); recover only when
  card is actually gone (cooldown, short stepwise revive)
- CommandPreview shows **before** any USB recover
- Scan: iw capped → iwlist → nmcli; if fresh scan empty, reuse `last_scan`
- IP-camera module (main → "Cameras"): stdlib only; Xiongmai-heavy Shopee cams;
  unauth → vuln probes → brand default creds; JPEG on LCD

### To test after pull on Pi
```bash
cd ~/C && git fetch origin && git reset --hard origin/main   # prefer reset if pull corrupts
sudo systemctl restart wifibox
systemctl status wifibox
iwconfig                          # external USB should show (often wlan1)
tail -f ~/C/wifibox.log
```

### Dependency notes
- **Attack tools:** aircrack-ng, aireplay-ng, airodump-ng, reaver, bully, hcxdumptool
  (+ hcxpcapngtool), **pixiewps**, **wpasupplicant** (OneShot)
- **Vendored:** `tools/oneshot/` = OneShot-C (`nikita-yfh/OneShot-C`) — compiled
  binary `oneshot` (needs `build-essential`; built by `install.sh`). No pip.
- **Tool versions (current via apt):** reaver 1.6.6 (t6x fork), pixiewps 1.4.2,
  bully 1.4 — no from-source builds needed.
- **UI/HW:** python3, python3-pil, lgpio, spidev, smbus2 (INA219). No numpy.
- Not installed on purpose: wifite2, hashcat, macchanger, dnsutils, pip/setuptools,
  DKMS RTL8192EU (in-tree `rtl8xxxu` / rtw88 handles monitor on 6.x).

### File map
```
main.py              entry — splash → buttons → menu
config.py            pins, presets (PIXIE Rush), HW defaults
cracked_store.py     cracked.json + upload_state + merge_by_bssid
install.sh           fresh-Pi setup (+ pixiewps, oneshot path, USB watchdog)
finalize.sh          cleanup for SD card imaging
hardware/display     LCD wrapper (headless fallback)
hardware/buttons     lgpio GPIO (chip fallback 4→0→1)
hardware/battery     INA219
ui/theme             6 themes, shadowed text, progress_bar, marquee
ui/screens           splash, AttackStatus, ProgressView, CommandPreview
ui/menus             menus; run_and_show = preview then attack
attack/interface     sysfs IF list; monitor; ensure_external(recover=…)
attack/scanner       iw→iwlist→nmcli; sort 2.4+WPS first
attack/orchestrator  targets → WPS/WPA/PMKID; post-list GETPSK retry; run-metric
attack/wps           reaver pixie → OneShot → GETPSK (no vendor PIN / no online BF)
attack/oneshot_wps   OneShot-C subprocess (managed-mode pixie)
attack/wps_log       wps-method / wps-ap helpers + run_id
attack/usb_watchdog.sh  rtw88 wedge → USB reset (systemd service)
attack/wpa           handshake + deauth
attack/pmkid         hcxdumptool path
attack/crack         manual on-box or upload
attack/camera*       IP-cam discovery/creds/vulns
camera_store.py      cameras.json
network/connect      wpa primary; restore home on fail
network/tailscale    status / up / ping
network/upload       SCP to 100.124.251.39:/home/sun/handshake/
tools/oneshot/       vendored OneShot-C (source + Makefile + vulnwsc.txt)
LCD_1in44.py / INA219.py
```

### GPIO pin map (BCM)
```
KEY_UP=6  KEY_DOWN=19  KEY_LEFT=5  KEY_RIGHT=26  KEY_PRESS=13
KEY1=21   KEY2=20      KEY3=16
LCD_RST=27  LCD_DC=25  LCD_BL=24
SPI: SCLK=11 MOSI=10 CS=8 (CE0)
```

### External USB (RTL8822BU) — why it “dies”
Sustained monitor/reaver wedges firmware; dmesg often shows `leave idle/ips failed`,
`failed to download firmware`, USB **error -71**, then device gone from bus.
Soft recover (modprobe/unbind/buspower) only helps early; hard death needs
**physical replug** or full reboot. Watchdog: `attack/usb_watchdog.sh`.
Optional harden: `options rtw88_core disable_lps_deep=Y` +
`options rtw88_usb switch_usb_mode=N` in `/etc/modprobe.d/rtw88.conf`.

### Known issues / ops lessons
- Pixie PIN without PSK is not connectable — use Cracked → Recover PSK (or wait
  for post-list GETPSK retry).
- Main loop blocks during attacks; KEY1/KEY3 stop via monitor thread.
- **Never** auto-run full USB recover before CommandPreview / every menu —
  it made the UI feel hung (~10–20s).
- OneShot must use **external** managed iface (not `wlan0`); matching
  `startswith("wlan")` previously stole the internal radio.
- Vendor PIN grind removed — it stuck Rush on AP #1 for minutes.
- “no targets” is usually **scan empty** (`iw` timeout on wedged USB), not
  filters — check `targets: scan=0` in log; Scan & Attack first fills last_scan.
- **Pi git corruption:** failed pull can zero `.py` files → crash loop, blank
  menu. Fix: `git fetch && git reset --hard origin/main` or reclone; preserve
  `cracked.json`. Do not leave empty objects in `.git/objects`.
- Branches `main` and `ipcam` should stay fast-forward synced after pushes.
