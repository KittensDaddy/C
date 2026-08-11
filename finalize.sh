#!/usr/bin/env bash
#
# finalize.sh — prepare this Pi for imaging. Clears logs, SSH keys, history,
# zeroes free space (better gzip/xz compression), then shuts down.
#
# Run AFTER you've fully tested the box and want to clone the SD card.
#
#   sudo bash finalize.sh
#
# After shutdown, pull the SD, plug into another Linux machine, and run:
#
#   sudo dd if=/dev/sdX of=wifibox.img bs=4M status=progress
#   sudo pishrink.sh -z wifibox.img       #  -> wifibox.img.gz
#
#   # PiShrink: https://github.com/Drewsif/PiShrink
#
set -euo pipefail

say()  { echo -e "\e[1;34m[finalize]\e[0m $*"; }
ok()   { echo -e "\e[1;32m  ✓\e[0m $*"; }

need_root() {
    if [[ $EUID -ne 0 ]]; then
        echo "Run as root: sudo bash finalize.sh"
        exit 1
    fi
}

main() {
    need_root

    echo
    echo -e "\e[1;31m  ╔══════════════════════════════════════╗\e[0m"
    echo -e "\e[1;31m  ║   FINALIZE — wipes for imaging       ║\e[0m"
    echo -e "\e[1;31m  ║   The Pi will SHUT DOWN after this.  ║\e[0m"
    echo -e "\e[1;31m  ╚══════════════════════════════════════╝\e[0m"
    echo
    read -r -p "  Continue? [y/N] " resp
    [[ "$resp" =~ ^[yY] ]] || { say "Aborted."; exit 1; }

    say "Stopping wifi-box service..."
    systemctl stop wifibox 2>/dev/null || true
    systemctl disable wifibox 2>/dev/null || true
    ok "stopped"

    say "Clearing bash history..."
    rm -f /root/.bash_history /home/sun/.bash_history /home/pi/.bash_history
    history -c 2>/dev/null || true
    ok "cleared"

    say "Clearing SSH host keys..."
    rm -f /etc/ssh/ssh_host_*
    ok "cleared (regenerate on first boot)"

    say "Clearing logs..."
    find /var/log -type f -exec sh -c '> "$1"' _ {} \; 2>/dev/null || true
    journalctl --rotate 2>/dev/null || true
    journalctl --vacuum-time=1s 2>/dev/null || true
    ok "cleared"

    say "Clearing /tmp and apt cache..."
    rm -rf /tmp/* /var/tmp/* 2>/dev/null || true
    apt-get clean 2>/dev/null || true
    ok "cleared"

    say "Zeroing free space (takes a while, improves compression)..."
    dd if=/dev/zero of=/zero.fill bs=1M 2>/dev/null || true
    rm -f /zero.fill
    ok "zeroed"

    say "Syncing..."
    sync
    ok "done"

    echo
    say "Shutting down in 5 seconds. Pull the SD card, then:"
    echo
    echo "  On another Linux machine:"
    echo "    sudo dd if=/dev/sdX of=wifibox.img bs=4M status=progress"
    echo "    sudo pishrink.sh -z wifibox.img       #  -> wifibox.img.gz"
    echo
    echo "  Burn to new SD:"
    echo "    sudo dd if=wifibox.img.gz of=/dev/sdX bs=4M status=progress conv=fsync"
    echo
    sleep 5
    shutdown -h now
}

main "$@"
