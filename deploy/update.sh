#!/usr/bin/env bash
# Jack in a Box auto-updater.  Run hourly by systemd (jack-in-a-box-update.timer) as the install user.
#
#   deploy/update.sh            check GitHub; install the update if there is one (when auto-update is on)
#   deploy/update.sh --force    same, even if auto-update is switched off (the web page's "Update now")
#
# What it does when the branch you installed from has new commits:
#   1. git fetch + reset to the new commit      (your settings/history in data/ are untouched)
#   2. reinstall Python packages only if requirements.txt changed
#   3. restart the service, then wait for the web page to answer
#   4. if it doesn't come up: ROLL BACK to the previous commit, restart, and remember the bad commit
#      so it isn't retried every hour until you push a fix.
# The result is written to data/update-status.json and shown on the Account tab.
set -uo pipefail

# Everything lives in main() so that replacing this very file mid-run (step 1) can't confuse bash.
main() {
  local DIR DATA STATUS BAD FLAG PY SYSTEMCTL SERVICE REPO BRANCH OLD NEW out FORCE=0 PORT
  DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  DATA="${JACK_IN_A_BOX_DATA:-$DIR/data}"
  STATUS="$DATA/update-status.json"; BAD="$DATA/update-bad-commit"; FLAG="$DATA/update-now"
  PY="$DIR/.venv/bin/python"; [[ -x "$PY" ]] || PY="$(command -v python3)"
  SYSTEMCTL="${SYSTEMCTL:-$(readlink -f "$(command -v systemctl)")}"
  SERVICE="${SERVICE:-jack-in-a-box.service}"
  export GIT_TERMINAL_PROMPT=0
  mkdir -p "$DATA"

  status() {  # status RESULT MESSAGE [COMMIT]
    "$PY" - "$STATUS" "$1" "$2" "${3:-}" <<'PYEOF'
import json, os, sys, time
path, result, msg, commit = sys.argv[1:5]
json.dump({"checked": time.strftime("%Y-%m-%d %H:%M:%S"), "result": result, "message": msg, "commit": commit},
          open(path + ".tmp", "w"))
os.replace(path + ".tmp", path)
PYEOF
    echo "update: $1 - $2"
  }
  cfg() {     # cfg dotted.key default
    "$PY" - "$DATA/config.json" "$1" "$2" <<'PYEOF'
import json, sys
try:
    cur = json.load(open(sys.argv[1]))
except Exception:
    cur = {}
for k in sys.argv[2].split("."):
    cur = cur.get(k) if isinstance(cur, dict) else None
print(sys.argv[3] if cur is None else cur)
PYEOF
  }
  healthy() {
    local i
    for i in $(seq 1 "${UPDATE_HEALTH_SECONDS:-45}"); do
      curl -fsS -m 3 "http://127.0.0.1:$PORT/api/state" >/dev/null 2>&1 && return 0
      sleep 1
    done
    return 1
  }
  pip_install() { "$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt" >"$DATA/update-pip.log" 2>&1; }
  restart() { sudo -n "$SYSTEMCTL" restart "$SERVICE" 9>&-; }   # 9>&-: the restarted app must not inherit our lock
  rollback() {  # rollback REASON
    echo "$NEW" >"$BAD"
    git -C "$REPO" reset --hard -q "$OLD"
    pip_install || true            # put the old packages back too (harmless if unchanged)
    restart || true
    status rolled_back "Update ${NEW:0:7} failed ($1) and was rolled back to ${OLD:0:7}. It won't be retried until you push a newer commit." "${OLD:0:7}"
    exit 1
  }

  exec 9>"$DATA/.update.lock"
  flock -n 9 || exit 0            # another update is already running

  [[ "${1:-}" == "--force" ]] && FORCE=1
  if [[ -e "$FLAG" ]]; then FORCE=1; rm -f "$FLAG"; fi
  if [[ $FORCE -eq 0 && "$(cfg updates.enabled True)" != "True" ]]; then exit 0; fi

  REPO="$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null)" || {
    status error "Installed without git (from a ZIP), so it can't auto-update. Reinstall from a git clone - see the README."; exit 1; }
  BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
  if [[ "$BRANCH" == "HEAD" ]]; then status error "Not on a branch, so there is nothing to follow."; exit 1; fi
  OLD="$(git -C "$REPO" rev-parse HEAD)"

  if ! out="$(git -C "$REPO" fetch --quiet origin "$BRANCH" 2>&1)"; then
    status error "Couldn't reach GitHub (${out##*$'\n'}). If the repo is private, the access token may have expired - see the README." "${OLD:0:7}"
    exit 1
  fi
  NEW="$(git -C "$REPO" rev-parse "origin/$BRANCH")"
  if [[ "$OLD" == "$NEW" ]]; then status up_to_date "Up to date." "${OLD:0:7}"; exit 0; fi
  if [[ $FORCE -eq 0 && "$(cat "$BAD" 2>/dev/null)" == "$NEW" ]]; then
    status error "Skipping ${NEW:0:7}: it failed to start earlier and was rolled back. Push a fix to try again." "${OLD:0:7}"
    exit 0
  fi

  echo "update: ${OLD:0:7} -> ${NEW:0:7}"
  git -C "$REPO" reset --hard -q "$NEW" || { status error "Couldn't switch to the new version." "${OLD:0:7}"; exit 1; }
  if ! git -C "$REPO" diff --quiet "$OLD" "$NEW" -- "$DIR/requirements.txt"; then
    echo "update: requirements changed, installing packages"
    pip_install || rollback "installing new packages failed"
  fi

  PORT="$(cfg web.port 8080)"
  if ! restart; then
    status error "Updated the files to ${NEW:0:7} but couldn't restart the service (re-run deploy/install.sh once to fix permissions)." "${NEW:0:7}"
    exit 1
  fi
  if healthy; then
    rm -f "$BAD"
    status updated "Updated ${OLD:0:7} -> ${NEW:0:7}: $(git -C "$REPO" log -1 --format=%s "$NEW")" "${NEW:0:7}"
    exit 0
  fi
  rollback "the app didn't start"
}

main "$@"
exit $?
