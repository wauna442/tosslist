#!/bin/bash
# Installs Tosslist on a Raspberry Pi running Raspberry Pi OS Lite.
# Run it with:  sudo bash /boot/firmware/tosslist/install.sh
set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash $0"
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/tosslist

echo "==> Installing software the scanner needs (this takes a few minutes)..."
apt-get update
apt-get install -y python3-evdev python3-flask python3-requests \
  python3-gpiozero python3-cryptography network-manager dnsmasq-base avahi-daemon

echo "==> Copying the program to $APP"
mkdir -p "$APP" /var/lib/tosslist
cp "$SRC"/*.py "$APP"/
chmod 700 /var/lib/tosslist   # keeps your Todoist token private

echo "==> Making the setup page pop up automatically on phones"
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
echo "address=/#/10.42.0.1" > /etc/NetworkManager/dnsmasq-shared.d/tosslist-captive.conf

echo "==> Setting both programs to start on every power-up"
cp "$SRC"/systemd/tosslist.service /etc/systemd/system/
cp "$SRC"/systemd/tosslist-setup.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable tosslist.service tosslist-setup.service
systemctl restart tosslist-setup.service tosslist.service

echo
echo "All done. On your phone or computer (on your home WiFi), open:"
echo "    http://$(hostname).local"
echo "and paste in your Todoist API token."
