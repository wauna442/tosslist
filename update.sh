#!/bin/bash
# Updates Tosslist from GitHub.
#
#   sudo bash ~/tosslist/update.sh            update to the newest release (e.g. v26.11.0)
#   sudo bash ~/tosslist/update.sh --main     update to the newest reviewed change on main
#   sudo bash ~/tosslist/update.sh --check    only say whether an update is available
#
# Read-only mode (issue #7): if it's on, the Pi turns it off, restarts,
# installs the update by itself, turns read-only back on and restarts again.
# If there's nothing new, nothing is restarted.
set -Eeuo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$REPO/scripts/lib.sh"

CHANNEL=release
RESUMED=""
CHECK_ONLY=""
for arg in "$@"; do
  case "$arg" in
    --main) CHANNEL=main ;;
    --release) CHANNEL=release ;;
    --check) CHECK_ONLY=1 ;;
    --resumed) RESUMED=1 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done
require_root "$@"

current_version() {
  as_owner_git "$REPO" describe --tags --always 2>/dev/null || echo unknown
}

on_error() {
  write_status failed "The update couldn't finish (often: no internet). Tosslist is unchanged." "$(current_version)"
  if [ -n "$RESUMED" ] && readonly_wanted; then readonly_on_and_restart; fi
}
trap on_error ERR

# Downloading what's new only touches the repository's history, which is
# fine even in read-only mode (it lands in RAM and is fetched again later).
echo "==> Checking GitHub for updates..."
as_owner_git "$REPO" fetch --quiet --tags --prune origin

if [ "$CHANNEL" = main ]; then
  target="origin/main"
  label="the newest change on main"
else
  target="$(as_owner_git "$REPO" tag -l 'v[0-9]*' --sort=-v:refname | head -n 1)"
  label="$target"
  if [ -z "$target" ]; then
    write_status current "No releases have been published yet." "$(current_version)"
    echo "No releases have been published yet."
    exit 0
  fi
fi

head="$(as_owner_git "$REPO" rev-parse HEAD)"
want="$(as_owner_git "$REPO" rev-parse "$target^{commit}")"
if [ "$head" = "$want" ]; then
  write_status current "Up to date." "$(current_version)"
  echo "Up to date ($(current_version))."
  # A resumed run with nothing to do still has to switch read-only back on.
  if [ -n "$RESUMED" ] && readonly_wanted; then readonly_on_and_restart; fi
  exit 0
fi
if ! as_owner_git "$REPO" merge-base --is-ancestor "$head" "$want"; then
  # This copy is newer than (or different from) the target: never go backwards.
  write_status current "This device is ahead of $label, so nothing was changed." "$(current_version)"
  echo "This device is ahead of $label, so nothing was changed."
  if [ -n "$RESUMED" ] && readonly_wanted; then readonly_on_and_restart; fi
  exit 0
fi
if [ -n "$CHECK_ONLY" ]; then
  echo "Update available: $label (this device has $(current_version))."
  exit 0
fi
if ! as_owner_git "$REPO" diff --quiet || ! as_owner_git "$REPO" diff --cached --quiet; then
  write_status failed "Files in $REPO were edited by hand, so the update was skipped." "$(current_version)"
  echo "Files in $REPO were edited by hand, so the update was skipped."
  if [ -n "$RESUMED" ] && readonly_wanted; then readonly_on_and_restart; fi
  exit 1
fi

if readonly_active; then
  schedule_resume "$REPO/update.sh" "--$CHANNEL"
  readonly_off_and_restart
  exit 0
fi

echo "==> Updating to $label..."
if [ "$CHANNEL" = main ]; then
  as_owner_git "$REPO" checkout --quiet -B main origin/main
else
  as_owner_git "$REPO" checkout --quiet --detach "$target"
fi

# The installer (from the new version) finishes the job, including turning
# read-only mode back on and restarting.
trap - ERR
if [ -n "$RESUMED" ]; then
  exec bash "$REPO/install.sh" --resumed
else
  exec bash "$REPO/install.sh"
fi
