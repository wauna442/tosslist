# shellcheck shell=bash
# Shared helpers for install.sh, update.sh and resume.sh (issue #7).
#
# Read-only mode is Raspberry Pi OS's overlay file system: with it on, the
# SD card isn't written to, so a power cut can't damage it. Turning it on or
# off only takes effect after a restart, so changing the device works like
# this:
#
#   read-only on  -> leave a note on the boot partition, turn read-only off,
#                    restart
#   at power-up   -> resume.sh finds the note and runs the same command again
#   read-only off -> make the change, turn read-only back on, restart
#
# For testing, TOSSLIST_DRY_RUN=<file> records system-changing commands in
# that file instead of running them.

if [ -n "${FW_DIR:-}" ]; then :
elif [ -d /boot/firmware ]; then FW_DIR=/boot/firmware
else FW_DIR=/boot
fi
PROC_CMDLINE="${PROC_CMDLINE:-/proc/cmdline}"
LOWER_ROOT="${TOSSLIST_LOWER_ROOT:-/media/root-ro}"
SYSTEM_DATA_DIR="${TOSSLIST_DATA_DIR:-/var/lib/tosslist}"
MARKER="$FW_DIR/tosslist-resume"

run() {
  if [ -n "${TOSSLIST_DRY_RUN:-}" ]; then
    echo "$*" >> "$TOSSLIST_DRY_RUN"
  else
    "$@"
  fi
}

require_root() {
  if [ "$(id -u)" -ne 0 ] && [ -z "${TOSSLIST_DRY_RUN:-}" ]; then
    echo "Please run with sudo:  sudo bash $0 $*"
    exit 1
  fi
}

readonly_active() {
  grep -qw "overlayroot=tmpfs" "$PROC_CMDLINE" 2>/dev/null
}

# The household's choice, kept with Tosslist's data: if this file exists,
# read-only mode stays off.
readonly_wanted() {
  [ ! -e "$(data_dir)/readonly-off" ]
}

# Where Tosslist's data lives right now (see common.py).
data_dir() {
  if readonly_active && [ -z "${TOSSLIST_DATA_DIR:-}" ]; then
    echo "$LOWER_ROOT$SYSTEM_DATA_DIR"
  else
    echo "$SYSTEM_DATA_DIR"
  fi
}

# Record the result for the settings page: write_status ok|failed|current "message" "version"
write_status() {
  local dir; dir="$(data_dir)"
  local unlock=""
  if readonly_active && [ -z "${TOSSLIST_DATA_DIR:-}" ]; then unlock=1; fi
  [ -n "$unlock" ] && run mount -o remount,rw "$LOWER_ROOT"
  mkdir -p "$dir"
  printf '{"time": "%s", "result": "%s", "message": "%s", "version": "%s"}\n' \
    "$(date '+%Y-%m-%d %H:%M')" "$1" "$2" "${3:-}" > "$dir/update-status.json.tmp"
  sync
  mv "$dir/update-status.json.tmp" "$dir/update-status.json"
  sync
  [ -n "$unlock" ] && run mount -o remount,ro "$LOWER_ROOT"
  return 0
}

# Leave a note so resume.sh runs this script again after the restart.
schedule_resume() {
  local script="$1"; shift
  printf '%s\n%s\n' "$script" "$*" > "$MARKER"
  sync
}

readonly_off_and_restart() {
  echo
  echo "Read-only mode is on. To make this change, the Pi will now:"
  echo "  1. turn read-only mode off and restart,"
  echo "  2. finish by itself (the settings page is unavailable meanwhile),"
  echo "  3. turn read-only mode back on and restart again."
  echo "This takes a few minutes. Your SSH connection will close now."
  run raspi-config nonint disable_overlayfs
  run systemctl reboot
}

readonly_on_and_restart() {
  echo
  echo "Turning read-only mode back on. The Pi will restart once more and"
  echo "be ready to scan in a couple of minutes."
  run raspi-config nonint enable_overlayfs
  run systemctl reboot
}

# Run a git command as whoever owns the repository, not root, so the
# files stay theirs.
as_owner_git() {
  local repo="$1"; shift
  local owner; owner="$(stat -c %U "$repo")"
  if [ "$(id -u)" -eq 0 ] && [ "$owner" != "root" ]; then
    runuser -u "$owner" -- git -C "$repo" "$@"
  else
    git -C "$repo" "$@"
  fi
}
