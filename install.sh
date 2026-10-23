#!/bin/bash
# Installs Tosslist on a Raspberry Pi running Raspberry Pi OS Lite.
#
#   sudo bash ~/tosslist/install.sh                 install, then turn on read-only mode
#   sudo bash ~/tosslist/install.sh --no-readonly   install, and keep read-only mode off
#
# Read-only mode protects the SD card from power cuts (issue #7). If it's
# on when this runs, the Pi turns it off, restarts, finishes installing by
# itself, turns it back on and restarts again. Tosslist's settings are kept.
set -Eeuo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SRC/scripts/lib.sh"
APP="${TOSSLIST_APP_DIR:-/opt/tosslist}"

RESUMED=""
READONLY_CHOICE=""
for arg in "$@"; do
  case "$arg" in
    --resumed) RESUMED=1 ;;
    --no-readonly) READONLY_CHOICE=off ;;
    --readonly) READONLY_CHOICE=on ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done
require_root "$@"

# Never leave the device stuck with read-only mode off after a restart
# that was meant to be temporary.
on_error() {
  write_status failed "The install stopped with an error. Run it again over SSH to see what went wrong."
  if [ -n "$RESUMED" ] && readonly_wanted; then readonly_on_and_restart; fi
}
trap on_error ERR

if readonly_active; then
  case "$READONLY_CHOICE" in
    off) schedule_resume "$SRC/install.sh" --no-readonly ;;
    on)  schedule_resume "$SRC/install.sh" --readonly ;;
    *)   schedule_resume "$SRC/install.sh" ;;
  esac
  readonly_off_and_restart
  exit 0
fi

echo "==> Installing software Tosslist needs (this takes a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
run apt-get update
run apt-get install -y python3-evdev python3-flask python3-requests \
  python3-gpiozero python3-cryptography network-manager dnsmasq-base \
  avahi-daemon overlayroot tesseract-ocr

echo "==> Copying the program to $APP"
run mkdir -p "$APP/scripts" "$SYSTEM_DATA_DIR"
run cp "$SRC"/*.py "$APP"/
run cp "$SRC"/scripts/*.sh "$APP"/scripts/
run chmod 700 "$SYSTEM_DATA_DIR"   # keeps your Todoist token private

mkdir -p "$SYSTEM_DATA_DIR"
case "$READONLY_CHOICE" in
  off) touch "$SYSTEM_DATA_DIR/readonly-off" ;;
  on)  rm -f "$SYSTEM_DATA_DIR/readonly-off" ;;
esac

echo "==> Making the setup page pop up automatically on phones"
run mkdir -p /etc/NetworkManager/dnsmasq-shared.d
if [ -z "${TOSSLIST_DRY_RUN:-}" ]; then
  echo "address=/#/10.42.0.1" > /etc/NetworkManager/dnsmasq-shared.d/tosslist-captive.conf
fi

version="$(as_owner_git "$SRC" describe --tags --always 2>/dev/null || echo unknown)"
# The settings page shows the version and offers "Update now" (issue #12).
put_file "$version" "$APP/VERSION"
put_file "$SRC" "$APP/REPO"

echo "==> Setting Tosslist to start on every power-up, and check for updates nightly"
for unit in tosslist.service tosslist-setup.service tosslist-resume.service \
            tosslist-update.service tosslist-update.timer; do
  put_file "$(sed "s#@REPO@#$SRC#g" "$SRC/systemd/$unit")" "/etc/systemd/system/$unit"
done
run systemctl daemon-reload
run systemctl enable tosslist.service tosslist-setup.service tosslist-resume.service \
  tosslist-update.timer

write_status ok "Installed." "$version"

if readonly_wanted; then
  if [ -z "$RESUMED" ]; then
    echo
    echo "All done. After the restart, open this on your phone or computer"
    echo "(on your home WiFi):  http://$(hostname).local"
  fi
  readonly_on_and_restart
else
  run systemctl restart tosslist-setup.service tosslist.service
  echo
  echo "All done (read-only mode is off). On your phone or computer, open:"
  echo "    http://$(hostname).local"
fi
