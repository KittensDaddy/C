#!/bin/bash
# wifi-usb-watchdog: watches the kernel log for the rtw88_8822bu USB wedge
# signature (register-write failures that precede the adapter dropping off
# the bus entirely) and resets its USB port immediately, before it gets to
# the fully-unresponsive state where unbind/rebind no longer helps.
LOG=/var/log/wifi-usb-watchdog.log
LAST_RESET=0

# Find the external adapter by which wireless netdev actually sits on the USB
# bus (not by interface name — wlan0/wlan1/wl0 depend on udev rename timing,
# e.g. before the first post-install reboot the internal card can still be
# "wlan0" and the external one "wlan1"). The internal card is SDIO (mmc), so
# checking for a "/usb" segment in the resolved device path picks the right
# one regardless of naming.
resolve_usbdev() {
    local net devpath
    for net in /sys/class/net/*; do
        [ -e "$net/wireless" ] || continue
        devpath=$(readlink -f "$net/device" 2>/dev/null)
        case "$devpath" in
            */usb*) basename "$(dirname "$devpath")"; return ;;
        esac
    done
    echo "1-1"   # fallback: adapter already dropped off the bus entirely
}

reset_usb() {
    local dev
    dev=$(resolve_usbdev)
    echo "$(date '+%F %T') wedge signature seen, resetting USB device $dev" >> "$LOG"
    echo "$dev" > /sys/bus/usb/drivers/usb/unbind 2>>"$LOG"
    sleep 2
    echo "$dev" > /sys/bus/usb/drivers/usb/bind 2>>"$LOG"
}

journalctl -kf -o cat 2>/dev/null | while read -r line; do
    case "$line" in
        *rtw_usb_reg_sec*|*"failed to download firmware"*|*"leave idle state failed"*|*"leave ips state"*)
            now=$(date +%s)
            if [ $((now - LAST_RESET)) -ge 25 ]; then
                LAST_RESET=$now
                reset_usb
            fi
            ;;
    esac
done
