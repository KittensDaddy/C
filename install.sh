#!/usr/bin/env bash
#
# install.sh — one-shot setup for wifi-box v2 on a fresh Pi OS Bookworm/Trixie Lite.
#
#   sudo bash install.sh
#
# Idempotent: safe to re-run.
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
TOTAL_STEPS=11

say()  { echo -e "\e[1;34m[wifibox]\e[0m $*"; }
ok()   { echo -e "\e[1;32m     ✓\e[0m $*"; }
warn() { echo -e "\e[1;33m     !\e[0m $*"; }
fail() { echo -e "\e[1;31m     ✗\e[0m $*"; }
info() { echo -e "       $*"; }
step() {
    local n="$1"; shift
    echo
    echo -e "\e[1;34m──── [$n/$TOTAL_STEPS]\e[0m \e[1;37m$*\e[0m"
    echo
}

log()  { echo "$@" >> "$LOG"; }

export DEBIAN_FRONTEND=noninteractive

need_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Please run as root: sudo bash install.sh"
        exit 1
    fi
}

# ---- spinner (background) -------------------------------------------------
_spin_pid=""
_start_spin() {
    local chars='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    ( while true; do
        for ((i=0; i<${#chars}; i++)); do
            printf "\r       %s %s" "${chars:$i:1}" "$1"
            sleep 0.1
        done
      done
    ) &
    _spin_pid=$!
}
_end_spin() {
    if [[ -n "${_spin_pid:-}" ]] && kill -0 "$_spin_pid" 2>/dev/null; then
        kill "$_spin_pid" 2>/dev/null || true
        wait "$_spin_pid" 2>/dev/null || true
        printf "\r       %s %s\n" "✔" "$1"
    fi
    _spin_pid=""
}

# ---- [1] platform checks --------------------------------------------------
check_platform() {
    step 1 "Checking platform..."
    if [[ -f /proc/device-tree/model ]] && grep -qi raspberry /proc/device-tree/model; then
        ok "Raspberry Pi: $(tr -d '\0' < /proc/device-tree/model)"
    else
        warn "Not detected as a Raspberry Pi — continuing anyway."
    fi
    if ! command -v apt-get >/dev/null; then
        fail "apt-get not found."
        exit 1
    fi
    if grep -qiE "(bookworm|trixie)" /etc/os-release 2>/dev/null; then
        ok "OS: supported (Bookworm/Trixie)"
    else
        warn "Unsupported OS. Bookworm or Trixie recommended."
    fi
    local kern
    kern=$(uname -r 2>/dev/null)
    info "Kernel: ${kern:-unknown}"
    info "Arch:   $(uname -m)"
}

# ---- [2] hardware ---------------------------------------------------------
enable_hardware() {
    step 2 "Enabling SPI, I2C, and HAT button pull-ups..."
    local conf=""
    for c in /boot/firmware/config.txt /boot/config.txt; do
        [[ -f "$c" ]] && conf="$c" && break
    done
    if [[ -z "$conf" ]]; then
        warn "config.txt not found; enable SPI/I2C manually via raspi-config."
        return
    fi
    local added=0
    if ! grep -q "^dtparam=spi=on" "$conf"; then
        echo "dtparam=spi=on" >> "$conf"
        info "added dtparam=spi=on"
        added=1
    fi
    if ! grep -q "^dtparam=i2c_arm=on" "$conf"; then
        echo "dtparam=i2c_arm=on" >> "$conf"
        info "added dtparam=i2c_arm=on"
        added=1
    fi
    if ! grep -q "^gpio=6,19,5,26,13,21,20,16=pu" "$conf"; then
        echo "gpio=6,19,5,26,13,21,20,16=pu" >> "$conf"
        info "added gpio pull-ups for HAT buttons"
        added=1
    fi
    if [[ $added -eq 1 ]]; then
        ok "Hardware config updated ($conf)"
    else
        ok "Hardware config already up-to-date"
    fi
}

# ---- [3] packages ---------------------------------------------------------
install_packages() {
    step 3 "Installing system packages (this takes a few minutes)..."
    info "apt update..."
    apt-get update -y >> "$LOG" 2>&1 || warn "apt update had warnings"

    # Add Tailscale repo (Bookworm has it, but ensure latest)
    if ! dpkg -s tailscale >/dev/null 2>&1; then
        info "adding Tailscale repo..."
        curl -fsSL https://pkgs.tailscale.com/stable/raspbian/bookworm.noarmor.gpg \
            | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null 2>>"$LOG" || true
        curl -fsSL https://pkgs.tailscale.com/stable/raspbian/bookworm.tailscale-keyring.list \
            | tee /etc/apt/sources.list.d/tailscale.list >/dev/null 2>>"$LOG" || true
        apt-get update -y >> "$LOG" 2>&1 || true
    fi

    info "apt upgrade..."
    apt-get upgrade -y >> "$LOG" 2>&1 || warn "apt upgrade had warnings"

    local pkgs=(
        python3 python3-pip python3-setuptools
        python3-pil python3-numpy
        python3-lgpio python3-gpiozero python3-spidev python3-smbus2
        aircrack-ng reaver bully hashcat hcxtools
        tshark macchanger wireless-tools iw iproute2
        network-manager dnsutils curl git
        tailscale
    )
    local total=${#pkgs[@]}
    local i=0
    # install in small batches so progress is visible
    local batch=()
    for pkg in "${pkgs[@]}"; do
        batch+=("$pkg")
        i=$((i + 1))
        if [[ ${#batch[@]} -ge 4 ]] || [[ $i -eq $total ]]; then
            printf "\r       [%2d/%2d] installing: %-40s" "$i" "$total" "${batch[*]}"
            apt-get install -y --no-install-recommends "${batch[@]}" >> "$LOG" 2>&1 || warn "some packages may have failed"
            batch=()
        fi
    done
    echo
    ok "All packages processed (see $LOG for details)"
}

# ---- [4] wifite2 ----------------------------------------------------------
install_wifite() {
    step 4 "Installing wifite2 (kimocoder)..."
    if [[ ! -d "$WIFITE_DIR" ]]; then
        _start_spin "cloning wifite2..."
        if git clone --depth 1 "$REPO_URL" "$WIFITE_DIR" >> "$LOG" 2>&1; then
            _end_spin "wifite2 cloned"
        else
            _end_spin "clone failed"
            fail "git clone wifite2 failed"
            return
        fi
    else
        ok "wifite2 already cloned at $WIFITE_DIR"
    fi

    _start_spin "python3 setup.py install..."
    if ( cd "$WIFITE_DIR" && \
         python3 setup.py install >> "$LOG" 2>&1 ); then
        _end_spin "wifite2 installed"
    else
        _end_spin "install failed"
        fail "setup.py install failed (see $LOG)"
        return
    fi

    if command -v wifite >/dev/null; then
        local ver
        ver=$(wifite --version 2>/dev/null | head -1 || echo "?")
        ok "wifite2 ready ($ver)"
    else
        warn "wifite not found in PATH"
    fi
}

# ---- [5] internal wifi naming ---------------------------------------------
name_internal_wifi() {
    step 5 "Naming internal WiFi -> wl0..."
    local f="/etc/systemd/network/10-wlan_internal.link"
    cat > "$f" <<'EOF'
[Match]
Driver=brcmfmac

[Link]
Name=wl0
EOF
    info "wrote $f (applies after reboot)"
    ok "Internal wifi -> wl0"
}

# ---- [6] rtl8192eu driver (optional) ---------------------------------------
install_rtl() {
    step 6 "RTL8192EU driver (optional)..."
    read -r -p "       Install RTL8192EU USB driver? [y/N] " resp
    case "$resp" in
        y|Y|yes|YES)
            # Auto-detect correct kernel headers package (Pi 5 vs Pi 4)
            local kver kern_headers
            kver=$(uname -r)
            if apt-cache show "linux-headers-$kver" >/dev/null 2>&1; then
                kern_headers="linux-headers-$kver"
            elif apt-cache show linux-headers-rpi-v8 >/dev/null 2>&1; then
                kern_headers="linux-headers-rpi-v8"
            else
                kern_headers="raspberrypi-kernel-headers"
            fi
            info "Installing: $kern_headers build-essential dkms"
            apt-get install -y "$kern_headers" build-essential dkms >> "$LOG" 2>&1
            [[ -d /opt/rtl8192eu ]] && rm -rf /opt/rtl8192eu

            _start_spin "cloning driver..."
            if git clone --depth 1 "$RTL_URL" /opt/rtl8192eu >> "$LOG" 2>&1; then
                _end_spin "driver cloned"
            else
                _end_spin "clone failed"
                fail "git clone failed"
                return
            fi

            _start_spin "building via dkms (takes a while)..."
            if ( cd /opt/rtl8192eu && dkms add . && dkms install rtl8192eu/1.0 ) >> "$LOG" 2>&1; then
                _end_spin "dkms build OK"
            else
                _end_spin "dkms build failed"
                warn "dkms build may have failed (see $LOG)"
            fi

            echo "blacklist rtl8xxxu" > /etc/modprobe.d/rtl8xxxu.conf
            echo "options 8192eu rtw_power_mgnt=0 rtw_enusbss=0" > /etc/modprobe.d/8192eu.conf
            ok "RTL8192EU driver configured"
            ;;
        *) warn "Skipping RTL8192EU driver." ;;
    esac
}

# ---- [7] sudoers ----------------------------------------------------------
setup_sudoers() {
    step 7 "Configuring passwordless sudo..."
    local f="/etc/sudoers.d/wifibox"
    cat > "$f" <<EOF
$USER ALL=(ALL) NOPASSWD: /usr/sbin/airmon-ng, /usr/sbin/iw, /usr/sbin/iwconfig, /usr/bin/nmcli, /usr/sbin/ip, /usr/bin/macchanger, /usr/sbin/wifite, /usr/bin/wifite, /usr/bin/tailscale
EOF
    chmod 440 "$f"
    ok "sudoers: $f"
}

# ---- [8] systemd service --------------------------------------------------
setup_service() {
    step 8 "Installing auto-start service..."
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
    ok "wifibox.service enabled (auto-start on boot)"
}

# ---- [9] tailscale ----------------------------------------------------------
setup_tailscale() {
    step 9 "Tailscale setup..."
    if ! command -v tailscale >/dev/null; then
        warn "tailscale not installed."
        return
    fi
    if tailscale status >/dev/null 2>&1; then
        local ip
        ip=$(tailscale ip -4 2>/dev/null || echo "?")
        ok "tailscale up — IP: $ip"
    else
        warn "Run 'sudo tailscale up' to authenticate."
        warn "  Server: 100.124.251.39"
    fi
}

# ---- [10] project files ----------------------------------------------------
setup_project() {
    step 10 "Setting up project files..."
    mkdir -p "$PROJECT_DIR"
    if [[ -f "$PROJECT_DIR/main.py" ]]; then
        chown -R "$USER":"$USER" "$PROJECT_DIR" 2>/dev/null || true
        [[ -f "$PROJECT_DIR/cracked.json" ]] || echo '[]' > "$PROJECT_DIR/cracked.json"
        chown "$USER":"$USER" "$PROJECT_DIR/cracked.json" 2>/dev/null || true
        ok "Files at $PROJECT_DIR"
        local count
        count=$(find "$PROJECT_DIR" -name '*.py' | wc -l)
        info "$count Python files, $(du -sh "$PROJECT_DIR" 2>/dev/null | cut -f1) total"
    else
        warn "No main.py found at $PROJECT_DIR — did you clone the repo?"
    fi
}

# ---- [11] verification ------------------------------------------------------
verify() {
    step 11 "Verifying installation..."
    local pass=0 failc=0
    local check
    declare -A checks=(
        ["wifite"]="command -v wifite"
        ["aircrack-ng"]="command -v aircrack-ng"
        ["lgpio"]="python3 -c 'import lgpio'"
        ["spidev"]="python3 -c 'import spidev'"
        ["smbus2"]="python3 -c 'import smbus2'"
        ["PIL"]="python3 -c 'from PIL import Image'"
        ["tailscale"]="command -v tailscale"
        ["aresolve"]="command -v aircrack-ng"
    )
    # project import
    if python3 -c "import sys; sys.path.insert(0,'$PROJECT_DIR'); import main" 2>/dev/null; then
        ok "project imports OK"
        pass=$((pass+1))
    else
        fail "project import failed (see journal)"
        failc=$((failc+1))
    fi

    for label in wifite aircrack-ng lgpio spidev smbus2 PIL tailscale; do
        if eval "${checks[$label]}" 2>/dev/null; then
            ok "$label"
            pass=$((pass+1))
        else
            warn "$label"
        fi
    done

    echo
    info "---"
    info "Passed: $pass  |  Warnings: $failc"
    info "Full log: $LOG"
}

# ---- main -----------------------------------------------------------------
main() {
    need_root
    : > "$LOG"

    echo
    echo -e "\e[1;36m  ╔══════════════════════════════════╗\e[0m"
    echo -e "\e[1;36m  ║      wifi-box v2  installer      ║\e[0m"
    echo -e "\e[1;36m  ╚══════════════════════════════════╝\e[0m"
    echo

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

    echo
    echo -e "\e[1;32m  ╔══════════════════════════════════╗\e[0m"
    echo -e "\e[1;32m  ║      Install complete!           ║\e[0m"
    echo -e "\e[1;32m  ╚══════════════════════════════════╝\e[0m"
    echo
    read -r -p "  Reboot now to apply SPI/I2C + wl0? [Y/n] " resp
    case "$resp" in
        n|N|no) say "Reboot later: sudo reboot" ;;
        *) reboot ;;
    esac
}

main "$@"
