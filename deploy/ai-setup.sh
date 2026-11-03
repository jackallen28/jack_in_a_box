#!/usr/bin/env bash
# Installs Ollama and the small model used for AI email summaries.
#   bash deploy/ai-setup.sh [model]        (default model: qwen2.5:0.5b, about 400 MB)
# Needs a 64-bit OS. Run deploy/lean-mode.sh first on a 1 GB Pi.
set -euo pipefail

ARCH="${JIAB_ARCH:-$(uname -m)}"
MODEL="${1:-qwen2.5:0.5b}"
DRY="${JIAB_DRY_RUN:-}"
SUDO=""; [[ -z "$DRY" && $EUID -ne 0 ]] && SUDO="sudo"
run() { if [[ -n "$DRY" ]]; then echo "   (dry run) $*"; else "$@"; fi; }

if [[ "$ARCH" != "aarch64" && "$ARCH" != "x86_64" ]]; then
  cat <<MSG
This system is $ARCH (32-bit). Ollama only runs on 64-bit systems, so the AI can't be installed here.

A Raspberry Pi 3B+ can run 64-bit. To switch (about 15 minutes):
  1. Back up the app's settings:  cp -r ~/jack_in_a_box/data ~/jiab-data-backup   (copy it off the Pi)
  2. On your computer open Raspberry Pi Imager -> Raspberry Pi 3 -> "Raspberry Pi OS Lite (64-bit)".
     In the settings (gear icon) set the hostname, a user + password, Wi-Fi and enable SSH.
  3. Boot the Pi, reinstall Jack in a Box (README), copy your data folder back, then re-run this script.
Or leave the Pi as it is and point "AI: Ollama address" at another computer that runs Ollama.
MSG
  exit 1
fi

MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' "${JIAB_MEMINFO:-/proc/meminfo}")
if (( MEM_MB < 700 )); then echo "Only ${MEM_MB} MB of RAM: too little even for the 0.5b model. Use another computer for the AI."; exit 1; fi

echo "==> Installing Ollama (official installer from ollama.com)"
if command -v ollama >/dev/null 2>&1; then echo "   already installed: $(ollama --version 2>&1 | head -1)"
else run bash -c 'curl -fsSL https://ollama.com/install.sh | sh'; fi

echo "==> Tuning Ollama for a small Pi (one model at a time, free the RAM after each use)"
OVERRIDE='[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=0"
Environment="OLLAMA_MAX_QUEUE=4"
Nice=5'
if [[ -n "$DRY" ]]; then echo "   (dry run) would write /etc/systemd/system/ollama.service.d/jack-in-a-box.conf:"; echo "$OVERRIDE" | sed 's/^/      /'
else
  $SUDO mkdir -p /etc/systemd/system/ollama.service.d
  echo "$OVERRIDE" | $SUDO tee /etc/systemd/system/ollama.service.d/jack-in-a-box.conf >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now ollama
  $SUDO systemctl restart ollama
  sleep 3
fi

echo "==> Downloading the model: $MODEL"
run ollama pull "$MODEL"

echo
echo "Done. In the web page: Text & sorting -> Summary engine = Local AI -> Save -> 'Test AI summary'."
echo "Expect roughly 1-3 minutes per forwarded email on a 3B+ (first run is slowest while the model loads from the SD card)."
