#!/usr/bin/env bash
# One-line installer.  On the Pi:
#
#   curl -fsSL https://raw.githubusercontent.com/jackallen28/jack_in_a_box/main/deploy/bootstrap.sh | bash
#   ...add  -s -- --with-ai  after "bash" to set up local AI summaries too.
#
# (A private fork can't be downloaded this way - see the README for the git clone + token form.)
# Safe to run again later: it pulls the newest version, reinstalls and restarts the service.
set -euo pipefail

REPO="${REPO:-https://github.com/jackallen28/jack_in_a_box.git}"
BRANCH="${BRANCH:-main}"
DEST="${DEST:-$HOME/jack_in_a_box}"

sudo apt-get update -qq
sudo apt-get install -y -qq git

if [[ -d "$DEST/.git" ]]; then
  echo "==> Updating $DEST"
  git -C "$DEST" fetch --depth 1 origin "$BRANCH"
  git -C "$DEST" checkout -q "$BRANCH"
  git -C "$DEST" reset -q --hard "origin/$BRANCH"
else
  echo "==> Downloading to $DEST"
  git clone --depth 1 -b "$BRANCH" "$REPO" "$DEST"
fi

exec bash "$DEST/deploy/install.sh" "$@"
