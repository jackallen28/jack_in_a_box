#!/usr/bin/env bash
# One command to turn on local AI email summaries (Raspberry Pi 3B+, 64-bit OS):
#
#   bash deploy/enable-ai.sh              set everything up, then reboot when it says so
#   bash deploy/enable-ai.sh --reboot     ...and reboot automatically at the end
#
# It runs, in order:  lean-mode.sh (free RAM)  ->  ai-setup.sh (Ollama + the model)  ->  switches the app's
# (it does NOT switch the app over: you do that on the web page after testing).  It checks for a 64-bit OS FIRST and stops without changing anything if the Pi is
# 32-bit (that needs a reflash with Raspberry Pi OS Lite 64-bit - see the message it prints).
# Also available as part of the first install:  bash deploy/install.sh --with-ai
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="$(cd "$HERE/.." && pwd)"
ARCH="${JIAB_ARCH:-$(uname -m)}"
DRY="${JIAB_DRY_RUN:-}"
REBOOT=0
for a in "$@"; do case "$a" in --reboot) REBOOT=1 ;; esac; done

if [[ "$ARCH" != "aarch64" && "$ARCH" != "x86_64" ]]; then
  JIAB_ARCH="$ARCH" bash "$HERE/ai-setup.sh" || true      # prints how to move to 64-bit
  echo; echo "Nothing was changed."
  exit 1
fi

echo "==> 1/3  Freeing RAM (lean mode)"
bash "$HERE/lean-mode.sh"
echo
echo "==> 2/3  Installing Ollama and the model"
bash "$HERE/ai-setup.sh"
echo
echo "==> 3/3  Leaving the summary engine on the built-in summary (nothing switched on yet)"
echo "   The AI is installed but OFF. You turn it on yourself, once you've seen it work."

echo
echo "All set. REBOOT to finish (frees the RAM):   sudo reboot"
echo "Then open the web page ->  Text & sorting  ->  'Test AI summary'  ->  'Switch on AI mode'."
if [[ "$REBOOT" == 1 && -z "$DRY" ]]; then echo "Rebooting in 10 seconds (Ctrl-C to cancel)..."; sleep 10; sudo reboot; fi
