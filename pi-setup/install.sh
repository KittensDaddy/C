#!/usr/bin/env bash
#
# install.sh — RTL88x2BU driver + boot-speedup for Raspberry Pi
#
# Combines:
#   1. RTL8812BU/8822BU (0bda:b812) driver via morrownr 88x2bu DKMS
#   2. Boot-time trims: disable cloud-init, NetworkManager-wait-online,
#      and switch tailscale to on-demand (only start when you need it).
#
# Usage:  sudo ./install.sh
#
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Please run as root:  sudo ./install.sh" >&2
    exit 1
fi

# Resolve the non-root user (for aliases / clone location)
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
DRIVER_DIR="$REAL_HOME/88x2bu-20210702"
DRIVER_REPO="https://github.com/morrownr/88x2bu-20210702.git"

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# 0. Dependencies
# ---------------------------------------------------------------------------
log "Installing build dependencies (git dkms build-essential bc ...)"
apt-get update
apt-get install -y git dkms build-essential bc raspberrypi-kernel-headers \
    || apt-get install -y git dkms build-essential bc "linux-headers-$(uname -r)"

# ---------------------------------------------------------------------------
# 1. Give the compile enough RAM (low-memory Pis thrash on swap)
# ---------------------------------------------------------------------------
if [[ -f /etc/dphys-swapfile ]]; then
    CUR_SWAP="$(grep -E '^CONF_SWAPSIZE' /etc/dphys-swapfile | cut -d= -f2 || echo 0)"
    if [[ "${CUR_SWAP:-0}" -lt 1024 ]]; then
        log "Temporarily raising swap to 1024 MB for the build"
        ORIG_SWAP="$CUR_SWAP"
        dphys-swapfile swapoff || true
        sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
        dphys-swapfile setup
        dphys-swapfile swapon
    fi
fi

# ---------------------------------------------------------------------------
# 2. Fetch + build the driver
# ---------------------------------------------------------------------------
if [[ -d "$DRIVER_DIR/.git" ]]; then
    log "Updating existing driver checkout in $DRIVER_DIR"
    sudo -u "$REAL_USER" git -C "$DRIVER_DIR" pull --ff-only || true
else
    log "Cloning driver into $DRIVER_DIR"
    sudo -u "$REAL_USER" git clone "$DRIVER_REPO" "$DRIVER_DIR"
fi

# Set the ARM arch for the installer (arm64 vs arm32)
cd "$DRIVER_DIR"
if [[ "$(uname -m)" == "aarch64" ]]; then
    [[ -x ./ARM64_RPI.sh ]] && ./ARM64_RPI.sh || true
else
    [[ -x ./ARM_RPI.sh ]] && ./ARM_RPI.sh || true
fi

log "Building + installing driver via DKMS (this is SLOW on low-RAM Pis)"
# --noreboot / non-interactive if the installer supports it; fall back otherwise
if ./install-driver.sh NoPrompt 2>/dev/null; then
    :
else
    yes | ./install-driver.sh || {
        echo "Driver build failed. Check: /var/lib/dkms/rtl88x2bu/*/build/make.log" >&2
        exit 1
    }
fi

# ---------------------------------------------------------------------------
# 3. Restore original swap size
# ---------------------------------------------------------------------------
if [[ -n "${ORIG_SWAP:-}" && -f /etc/dphys-swapfile ]]; then
    log "Restoring swap to ${ORIG_SWAP} MB"
    dphys-swapfile swapoff || true
    sed -i "s/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=${ORIG_SWAP}/" /etc/dphys-swapfile
    dphys-swapfile setup
    dphys-swapfile swapon
fi

# ---------------------------------------------------------------------------
# 4. Boot-time speedups
# ---------------------------------------------------------------------------
log "Disabling cloud-init (unblocks early boot)"
touch /etc/cloud/cloud-init.disabled 2>/dev/null || true

log "Not blocking boot on Wi-Fi (NetworkManager-wait-online)"
systemctl disable NetworkManager-wait-online.service 2>/dev/null || true

log "Switching tailscale to on-demand (won't start at boot)"
systemctl disable tailscaled.service 2>/dev/null || true

# tsup / tsdown helper aliases
BASHRC="$REAL_HOME/.bashrc"
if [[ -f "$BASHRC" ]] && ! grep -q 'alias tsup=' "$BASHRC"; then
    log "Adding tsup / tsdown aliases to $BASHRC"
    cat >> "$BASHRC" <<'EOF'

# tailscale on-demand (added by pi-setup install.sh)
alias tsup='sudo systemctl start tailscaled && sudo tailscale up'
alias tsdown='sudo tailscale down && sudo systemctl stop tailscaled'
EOF
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
log "All done."
cat <<EOF

Driver status:
$(dkms status | grep 88x2bu || echo '  (not shown — check make.log if build failed)')

Next:
  sudo reboot

After reboot, verify:
  lsmod | grep 88x2bu
  iw dev
  systemd-analyze          # check the new boot time

Tailscale is now manual — run:  tsup   (up)   /   tsdown  (down)
EOF
