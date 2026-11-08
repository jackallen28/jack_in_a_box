#!/usr/bin/env bash
# Reboot the Pi every N days (default 2 = every 48 hours) at 3 am, so it never runs for weeks and any slow leak or odd state is cleared.
# Run by systemd (jack-in-a-box-reboot.timer) as root EVERY HOUR; it only acts in the chosen hour.  It works even if the app itself has hung,
# which is the point of a scheduled reboot.  The on/off, the number of days and the hour are the Settings tab's "Automatic reboot" card
# (stored in data/config.json).
#
#   bash deploy/auto-reboot.sh APP_DIR --dry-run     # say what it would do now, change nothing
#
# "Every 48 hours": it reboots when the Pi has been up at least N*24 - 2 hours (the 2 h of slack covers boot time), so a reboot you did yourself
# resets the count, and the nightly schedule never doubles up.  It waits (up to 30 minutes) for anything being printed to finish first.
set -u
DIR="${1:?usage: auto-reboot.sh APP_DIR [--dry-run]}"
DRY=0; [[ "${2:-}" == "--dry-run" ]] && DRY=1
CONF="$DIR/data/config.json"
DB="$DIR/data/reminders.db"
REBOOT_CMD="${JACK_REBOOT_CMD:-systemctl reboot}"
UPTIME_FILE="${JACK_UPTIME_FILE:-/proc/uptime}"
NOW_HOUR="${JACK_NOW_HOUR:-$(date +%H)}"
WAIT="${JACK_WAIT_SECONDS:-1800}"
log() { logger -t jack-in-a-box-reboot "$*" 2>/dev/null || true; echo "$*"; }

read -r ENABLED DAYS HOUR < <(python3 -c '
import json, sys
try:
    m = json.load(open(sys.argv[1])).get("maintenance", {})
except Exception:
    m = {}
def num(key, default, lo, hi):
    try:
        return min(hi, max(lo, int(m.get(key, default))))
    except Exception:
        return default
print(1 if m.get("reboot_enabled", True) else 0, num("reboot_every_days", 2, 1, 30), num("reboot_hour", 3, 0, 23))
' "$CONF")

if [[ "${ENABLED:-1}" != 1 ]]; then log "Automatic reboot is switched off."; exit 0; fi
if [[ $DRY == 0 && $((10#$NOW_HOUR)) != "$HOUR" ]]; then exit 0; fi          # the timer fires hourly; only the chosen hour counts
UP_H=$(awk '{print int($1/3600)}' "$UPTIME_FILE")
NEED=$(( ${DAYS:-2} * 24 - 2 ))
if (( UP_H < NEED )); then log "Up ${UP_H} h; the next automatic reboot is due once it has been up ${NEED} h (every ${DAYS} day(s) at ${HOUR}:00). Not yet."; exit 0; fi

pending() {
  python3 -c '
import sqlite3, sys
try:
    print(sqlite3.connect(sys.argv[1], timeout=5).execute("SELECT COUNT(*) FROM jobs WHERE status=?", ("pending",)).fetchone()[0])
except Exception:
    print(0)
' "$DB"
}
waited=0
while (( waited < WAIT )) && [[ "$(pending)" != 0 ]]; do
  [[ $DRY == 1 ]] && { log "Would wait: something is waiting to print."; break; }
  log "Something is still printing or waiting to print: waiting before the reboot."
  sleep "${JACK_SLEEP:-30}"; waited=$(( waited + ${JACK_SLEEP:-30} ))
done
if [[ $DRY == 1 ]]; then log "Would reboot now (up ${UP_H} h)."; exit 0; fi
log "Automatic reboot (up ${UP_H} h; every ${DAYS} day(s) at ${HOUR}:00)."
exec $REBOOT_CMD
