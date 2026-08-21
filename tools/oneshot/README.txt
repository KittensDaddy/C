OneShot-C (nikita-yfh/OneShot-C) vendored for wifi-box PIXIE Rush bake-off.

Replaces the dead drygdryg/kimocoder OneShot (Python). C rewrite of the same
approach: Pixie Dust + static/vendor PIN prediction in managed mode (no monitor)
via wpa_supplicant + pixiewps.

Build (on each arch, needs build-essential):
    make -C tools/oneshot

Binary: tools/oneshot/oneshot
Requires at runtime: pixiewps, wpa_supplicant, iw (all apt-installed).
vulnwsc.txt is the vulnerable-device list used for scan highlighting; the
attack passes --vuln-list so it works regardless of the process CWD.
