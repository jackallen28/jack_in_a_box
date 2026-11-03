#!/usr/bin/env bash
# One-line installer.  On the Pi:
#
#   curl -fsSL https://raw.githubusercontent.com/jackallen28/jackallen/BRANCH/jack-in-a-box/deploy/bootstrap.sh | bash
#
# (If the GitHub repo is private, use the git clone form in the README instead.)
# Safe to run again later: it pulls the newest version, reinstalls and restarts the service.
set -euo pipefail

REPO="${REPO:-https://github.com/jackallen28/jackallen.git}"
BRANCH="${BRANCH:-jack-in-a-box}"
DEST="${DEST:-$HOME/jack-in-a-box-src}"

sudo apt-get update -qq
sudo apt-get install -y -qq git

if [[ -d "$DEST/.git" ]]; then
  echo "==> Updating $DEST"
  git -C "$DEST" fetch --depth 1 origin "$BRANCH"
  git -C "$DEST" checkout -q "$BRANCH"
  git -C "$DEST" reset -q --hard "origin/$BRANCH"
else
  echo "==> Downloading to $DEST"
  git clone --depth 1 --filter=blob:none --sparse -b "$BRANCH" "$REPO" "$DEST"
  git -C "$DEST" sparse-checkout set jack-in-a-box
fi

exec bash "$DEST/jack-in-a-box/deploy/install.sh" "$@"
