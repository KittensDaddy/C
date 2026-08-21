#!/bin/bash
# wifi-usb-watchdog: watches the kernel log for the rtw88_8822bu USB wedge
# signature and resets the USB port before the adapter drops off the bus
# entirely. When it is already gone, power-cycle the Pi's USB controller.
LOG=/var/log/wifi-usb-watchdog.log
LAST_RESET=0
COOLDOWN=8
BUSPOWER=/sys/devices/platform/soc/3f980000.usb/buspower

# External adapter = wireless netdev sitting on USB (internal is SDIO/mmc).
resolve_usbdev() {
    local net devpath
    for net in /sys/class/net/*; do
        [ -e "$net/wireless" ] || continue
        devpath=$(readlink -f "$net/device" 2>/dev/null)
        case "$devpath" in
            */usb*)
                # .../1-1/1-1:1.0 -> 1-1
                basename "$(dirname "$devpath")"
                return
                ;;
        esac
    done
    echo "1-1"
}

usb_power_cycle() {
    if [ -w "$BUSPOWER" ]; then
        echo "$(date '+%F %T') buspower cycle" >> "$LOG"
        echo 0 > "$BUSPOWER" 2>>"$LOG"
        sleep 3
        echo 1 > "$BUSPOWER" 2>>"$LOG"
        sleep 2
    fi
}

reset_usb() {
    local dev bus num
    dev=$(resolve_usbdev)
    echo "$(date '+%F %T') wedge — reset USB $dev" >> "$LOG"

    # 1) USB reset while the device is still enumerable (authorized toggle +
    #    usbreset). Catches the early wedge before it drops off the bus.
    if [ -e "/sys/bus/usb/devices/$dev/authorized" ]; then
        echo 0 > "/sys/bus/usb/devices/$dev/authorized" 2>>"$LOG"
        sleep 1
        echo 1 > "/sys/bus/usb/devices/$dev/authorized" 2>>"$LOG"
        sleep 1
    fi
    bus=$(cat "/sys/bus/usb/devices/$dev/busnum" 2>/dev/null)
    num=$(cat "/sys/bus/usb/devices/$dev/devnum" 2>/dev/null)
    if [ -n "$bus" ] && [ -n "$num" ] && [ -x /usr/bin/usbreset ]; then
        /usr/bin/usbreset "$bus/$num" 2>>"$LOG"
        sleep 1
    fi

    # 2) Unbind/rebind the device.
    if [ -e "/sys/bus/usb/devices/$dev" ]; then
        echo "$dev" > /sys/bus/usb/drivers/usb/unbind 2>>"$LOG"
        sleep 2
        echo "$dev" > /sys/bus/usb/drivers/usb/bind 2>>"$LOG"
        sleep 2
    fi

    # 3) Netdev still missing -> full rtw88 reload + bus power (last resort).
    local found=0
    for net in /sys/class/net/*; do
        [ -e "$net/wireless" ] || continue
        case "$(readlink -f "$net/device" 2>/dev/null)" in
            */usb*) found=1; break ;;
        esac
    done
    if [ "$found" -eq 0 ]; then
        modprobe -r rtw88_8822bu rtw88_usb rtw88_8822b rtw88_core 2>>"$LOG"
        usb_power_cycle
        sleep 1
        modprobe rtw88_8822bu 2>>"$LOG"
    fi
}

journalctl -kf -o cat 2>/dev/null | while read -r line; do
    # Quoted substrings are literal (quotes are stripped by the case parser);
    # unquoted * are the globs. Do NOT unquote — bare spaces break the pattern.
    case "$line" in
        *rtw_usb_reg_sec*|*"failed to download firmware"*|*"idle state failed"*|*"ips state"*|*"device descriptor read"*"error -71"*|*"device not accepting address"*|*"USB disconnect"*|*"error -110"*|*"error -19"*)
            now=$(date +%s)
            echo "$(date '+%F %T') watchdog trigger: $line" >> "$LOG"
            if [ $((now - LAST_RESET)) -ge $COOLDOWN ]; then
                LAST_RESET=$now
                reset_usb
            fi
            ;;
    esac
done
