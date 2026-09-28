#!/usr/bin/env bash
# Installs the display overlay + status service on the Pi. Run with sudo, then reboot.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }

CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || CFG=/boot/config.txt   # pre-Bookworm layout
OVERLAY="dtoverlay=piscreen,speed=16000000,rotate=90"

apt-get update
apt-get install -y python3-pil fonts-dejavu-core

install -d /opt/hbdisplay
install -m 755 "$(dirname "$0")/hbdisplay.py" /opt/hbdisplay/hbdisplay.py
install -m 644 "$(dirname "$0")/hbdisplay.service" /etc/systemd/system/hbdisplay.service

if ! grep -q '^dtoverlay=piscreen' "$CFG"; then
  printf '\n# 3.5" SPI TFT (ILI9486 + XPT2046 touch)\n%s\n' "$OVERLAY" >> "$CFG"
  echo "Added '$OVERLAY' to $CFG"
fi

systemctl daemon-reload
systemctl enable hbdisplay.service
echo "Done. Reboot to load the display overlay: sudo reboot"
