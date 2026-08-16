#!/bin/bash
# wifi-usb-watchdog: watches the kernel log for the rtw88_8822bu USB wedge
# signature (register-write failures that precede the adapter dropping off
# the bus entirely) and resets its USB port immediately, before it gets to
# the fully-unresponsive state where unbind/rebind no longer helps.
IFACE=wlan0
LOG=/var/log/wifi-usb-watchdog.log
LAST_RESET=0

resolve_usbdev() {
    local devpath
    devpath=$(readlink -f "/sys/class/net/$IFACE/device" 2>/dev/null)
    [ -z "$devpath" ] && { echo "1-1"; return; }
    basename "$(dirname "$devpath")"
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
