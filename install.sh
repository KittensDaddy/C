#!/usr/bin/env bash
#
# install.sh — one-shot setup for wifi-box v2 on a fresh Pi OS Bookworm Lite.
#
#   sudo bash install.sh
#
# Idempotent: safe to re-run. Handles hardware enable, packages, wifite2,
# interface naming, sudoers, auto-start service, tailscale, and verification.
#
set -euo pipefail

# ---- config ---------------------------------------------------------------
PROJECT_DIR="/home/sun/C"
USER="${SUDO_USER:-pi}"
[ "$USER" = "root" ] && USER="pi"
REPO_URL="https://github.com/kimocoder/wifite2"
WIFITE_DIR="/opt/wifite2"
RTL_URL="https://github.com/Mange/rtl8192eu-linux-driver"
LOG="/tmp/wifibox-install.log"

say()  { echo -e "\e[1;34m[wifibox]\e[0m $*"; }
ok()   { echo -e "\e[1;32m  ✓\e[0m $*"; }
warn() { echo -e "\e[1;33m  !\e[0m $*"; }
fail() { echo -e "\e[1;31m  ✗\e[0m $*"; }

log()  { echo "$@" >> "$LOG"; }

need_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Please run as root: sudo bash install.sh"
        exit 1
    fi
}

# ---- [1] platform checks --------------------------------------------------
check_platform() {
    say "Checking platform..."
    if [[ -f /proc/device-tree/model ]] && grep -qi raspberry /proc/device-tree/model; then
        ok "Raspberry Pi detected"
    else
        warn "Not detected as a Raspberry Pi — continuing anyway."
    fi
    if ! command -v apt-get >/dev/null; then
        fail "apt-get not found. This installer targets Debian/Raspberry Pi OS."
        exit 1
    fi
    if grep -qiE "(bookworm|trixie)" /etc/os-release 2>/dev/null; then
        ok "OS: supported (Bookworm/Trixie)"
    else
        warn "Unsupported OS. Bookworm or Trixie recommended."
    fi
}

# ---- [2] hardware ---------------------------------------------------------
enable_hardware() {
    say "Enabling SPI, I2C, and HAT button pull-ups..."
    local conf=""
    for c in /boot/firmware/config.txt /boot/config.txt; do
        [[ -f "$c" ]] && conf="$c" && break
    done
    if [[ -n "$conf" ]]; then
        grep -q "^dtparam=spi=on" "$conf" || echo "dtparam=spi=on" >> "$conf"
        grep -q "^dtparam=i2c_arm=on" "$conf" || echo "dtparam=i2c_arm=on" >> "$conf"
        # 1.44" HAT buttons/joystick — no external pull-ups on the board
        grep -q "^gpio=6,19,5,26,13,21,20,16=pu" "$conf" || \
            echo "gpio=6,19,5,26,13,21,20,16=pu" >> "$conf"
        ok "SPI/I2C + GPIO pull-ups enabled in $conf"
    else
        warn "Could not find config.txt; enable SPI/I2C manually via raspi-config."
    fi
}

# ---- [3] packages ---------------------------------------------------------
install_packages() {
    say "Updating package lists..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y >> "$LOG" 2>&1 || true
    apt-get upgrade -y >> "$LOG" 2>&1 || true

    say "Installing system packages..."
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-pil python3-numpy \
        python3-lgpio python3-spidev python3-smbus2 \
        aircrack-ng reaver bully hashcat hcxtools \
        tshark macchanger wireless-tools iw iproute2 \
        network-manager dnsutils curl git \
        tailscale >> "$LOG" 2>&1 || true
    ok "Packages installed (see $LOG for details)"
}

# ---- [4] wifite2 ----------------------------------------------------------
install_wifite() {
    say "Installing wifite2 (kimocoder)..."
    if [[ ! -d "$WIFITE_DIR" ]]; then
        git clone --depth 1 "$REPO_URL" "$WIFITE_DIR" >> "$LOG" 2>&1 || \
            { fail "git clone wifite2 failed"; return; }
    fi
    # Detect pip --break-system-packages flag (PEP 668, Python 3.11+)
    local pip_break=""
    python3 -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages' && \
        pip_break="--break-system-packages"
    ( cd "$WIFITE_DIR" && \
      python3 -m pip install -e . $pip_break 2>&1 ) >> "$LOG" 2>&1 || \
        { warn "pip install wifite2 failed; ensure wifite is in PATH"; }
    command -v wifite >/dev/null && ok "wifite2 ready" || warn "wifite not found in PATH"
}

# ---- [5] internal wifi naming ---------------------------------------------
name_internal_wifi() {
    say "Naming internal WiFi -> wl0..."
    local f="/etc/systemd/network/10-wlan_internal.link"
    cat > "$f" <<EOF
[Match]
Driver=brcmfmac

[Link]
Name=wl0
EOF
    systemctl restart systemd-networkd >> "$LOG" 2>&1 || true
    ok "Internal wifi named wl0 (applies after reboot)"
}

# ---- [6] rtl8192eu driver (optional) ---------------------------------------
install_rtl() {
    read -r -p "Install RTL8192EU USB driver? [y/N] " resp
    case "$resp" in
        y|Y|yes|YES)
            say "Building RTL8192EU driver via dkms..."
            apt-get install -y raspberrypi-kernel-headers build-essential dkms >> "$LOG" 2>&1
            [[ -d /opt/rtl8192eu ]] && rm -rf /opt/rtl8192eu
            git clone --depth 1 "$RTL_URL" /opt/rtl8192eu >> "$LOG" 2>&1
            ( cd /opt/rtl8192eu && dkms add . && dkms install rtl8192eu/1.0 ) >> "$LOG" 2>&1 || warn "dkms build failed"
            echo "blacklist rtl8xxxu" > /etc/modprobe.d/rtl8xxxu.conf
            echo "options 8192eu rtw_power_mgnt=0 rtw_enusbss=0" > /etc/modprobe.d/8192eu.conf
            ok "RTL8192EU driver configured"
            ;;
        *) warn "Skipping RTL8192EU driver." ;;
    esac
}

# ---- [7] sudoers ----------------------------------------------------------
setup_sudoers() {
    say "Configuring passwordless sudo for attack commands..."
    local f="/etc/sudoers.d/wifibox"
    cat > "$f" <<EOF
$USER ALL=(ALL) NOPASSWD: /usr/sbin/airmon-ng, /usr/sbin/iw, /usr/sbin/iwconfig, /usr/bin/nmcli, /usr/sbin/ip, /usr/bin/macchanger, /usr/sbin/wifite, /usr/bin/wifite, /usr/bin/tailscale
EOF
    chmod 440 "$f"
    ok "sudoers configured"
}

# ---- [8] systemd service --------------------------------------------------
setup_service() {
    say "Installing auto-start service..."
    local f="/etc/systemd/system/wifibox.service"
    cat > "$f" <<EOF
[Unit]
Description=wifi-box v2
After=network.target multi-user.target

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
ExecStart=/usr/bin/python3 $PROJECT_DIR/main.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable wifibox.service >> "$LOG" 2>&1 || true
    ok "wifibox.service enabled"
}

# ---- [9] tailscale ----------------------------------------------------------
setup_tailscale() {
    say "Tailscale setup..."
    if command -v tailscale >/dev/null; then
        if tailscale status >/dev/null 2>&1; then
            ok "tailscale already up"
        else
            warn "Run 'sudo tailscale up' to authenticate, then verify with 'tailscale status'."
            warn "Target server: 100.124.251.39  (upload dir: /home/sun/handshake)"
        fi
    else
        warn "tailscale not installed."
    fi
}

# ---- [10] project files ----------------------------------------------------
setup_project() {
    say "Setting up project directory $PROJECT_DIR ..."
    mkdir -p "$PROJECT_DIR"
    # If this script lives inside the project, ensure cracked.json exists
    if [[ -f "$PROJECT_DIR/main.py" ]]; then
        chown -R "$USER":"$USER" "$PROJECT_DIR" || true
        [[ -f "$PROJECT_DIR/cracked.json" ]] || echo '[]' > "$PROJECT_DIR/cracked.json"
        chown "$USER":"$USER" "$PROJECT_DIR/cracked.json" || true
        ok "project files present"
    else
        warn "Project not found at $PROJECT_DIR — copy files there, then re-run."
    fi
}

# ---- [11] verification ------------------------------------------------------
verify() {
    say "Verifying installation..."
    command -v wifite >/dev/null && ok "wifite" || fail "wifite missing"
    python3 -c "import lgpio" 2>/dev/null && ok "lgpio" || fail "lgpio missing"
    python3 -c "import spidev" 2>/dev/null && ok "spidev" || fail "spidev missing"
    python3 -c "import smbus2" 2>/dev/null && ok "smbus2" || fail "smbus2 missing"
    python3 -c "from PIL import Image" 2>/dev/null && ok "PIL" || fail "PIL missing"
    python3 -c "import sys; sys.path.insert(0,'$PROJECT_DIR'); import main" 2>/dev/null \
        && ok "project imports OK" || fail "project import failed (see journal)"
    command -v tailscale >/dev/null && ok "tailscale" || warn "tailscale missing"
    command -v aircrack-ng >/dev/null && ok "aircrack-ng" || warn "aircrack-ng missing"
}

# ---- run ---------------------------------------------------------------------
main() {
    need_root
    : > "$LOG"
    check_platform
    enable_hardware
    install_packages
    install_wifite
    name_internal_wifi
    install_rtl
    setup_sudoers
    setup_service
    setup_tailscale
    setup_project
    verify

    say "Install complete."
    read -r -p "Reboot now to apply SPI/I2C + wl0 naming? [Y/n] " resp
    case "$resp" in
        n|N|no) say "Reboot later manually." ;;
        *) reboot ;;
    esac
}

main "$@"
