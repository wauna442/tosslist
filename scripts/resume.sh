#!/bin/bash
# Runs once at power-up (tosslist-resume.service). If an install or update
# had to restart to turn read-only mode off, finish it now (issue #7).
set -u
# shellcheck source=scripts/lib.sh
source "$(dirname "$0")/lib.sh"

[ -f "$MARKER" ] || exit 0
{ read -r script; read -r args; } < "$MARKER"
# Remove the note first, so a problem can never cause a restart loop.
rm -f "$MARKER"
sync

if readonly_active; then
  # Turning read-only off didn't take effect. Don't try again; record it.
  write_status failed "Read-only mode couldn't be turned off, so the update didn't run."
  exit 0
fi
if [ ! -f "$script" ]; then
  write_status failed "Couldn't find $script to finish the update."
  readonly_wanted && readonly_on_and_restart
  exit 0
fi

# shellcheck disable=SC2086
exec bash "$script" $args --resumed
