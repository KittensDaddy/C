
- ~~show currently attack ESSID~~
- ~~Scan wifi and select for specific attack~~
- a case
- battery life test
- performance and stability test
- temperature test
- ~~better ui~~
- ~~stealth mode~~
- screensaver
- ~~no wlan0 default~~
- clean up code
- properly handle each function
- ~~crack wpa upload auto connect cracked wifi upload via tailscale~~
- ~~GIF embeded~~
- ~~scrolling faster the longer press~~
- ~~add progress tracking when attack~~
- ~~reduce boot time~~
- ~~additional menu for selected wifi attack mode~~
- ~~migrate from RPi.GPIO to LGPIO to use bookworm~~ no need for now
- highlight cracked or handshake capture ssid when scan for attack of exclude
- progress bar update for all attack
- use internal_wifi driver that enable monitor mode
- seperate each menu page into files
- rewrite scan network to use wash -a command and mark wps for specific attack to know to use or not to use wps
----
use bookworm but sudo apt install python3-rpi.gpio
----
- use crontab with tmux crontab -e >> /usr/bin/tmux new-seesion -d -s 6 '/usr/bin/python3 /path/to/pyhton.py'
----
- sudo apt update && sudo apt upgrade -y
- sudo apt install git python3-pip python3-venv
- 
- sudo python3 -m venv venv
- source venv/bin/activate
- pip3 install -r requirement.txt
- sudo python3 setup.py install
- sudo apt install aircrack-ng reaver bully hashcat tshark wireshark macchanger hcxtools
----
rtl81982eu
https://github.com/Mange/rtl8192eu-linux-driver
----
- sudo apt-get install git raspberrypi-kernel-headers build-essential dkms -y
- git clone https://github.com/Mange/rtl8192eu-linux-driver
- cd rtl8192eu-linux-driver
- nano Makefile
- sudo dkms add .
- sudo dkms install rtl8192eu/1.0
- echo "blacklist rtl8xxxu" | sudo tee /etc/modprobe.d/rtl8xxxu.conf
- echo -e "8192eu\n\nloop" | sudo tee /etc/modules
- echo "options 8192eu rtw_power_mgnt=0 rtw_enusbss=0" | sudo tee /etc/modprobe.d/8192eu.conf
- sudo update-grub; sudo update-initramfs -u
- sudo reboot
----
naming internal wifi
----
- sudo nano /etc/systemd/network/10-wlan_internal.link
----
- [Match]
- Driver=brcmfmac
- [Link]
- Name=wl0
----
- sudo systemctl restart systemd-networkd
- sudo reboot
----


