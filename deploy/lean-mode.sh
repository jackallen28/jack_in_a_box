#!/usr/bin/env bash
# "Super basic mode" for a Raspberry Pi 3B+ (1 GB RAM): frees as much memory as possible for the local AI.
#
#   bash deploy/lean-mode.sh          apply  (then reboot)
#   bash deploy/lean-mode.sh --undo   put everything back
#
# What it changes (all reversible):
#   * GPU memory 16 MB     - a headless printer box needs no graphics memory (frees ~48-64 MB)
#   * Bluetooth off        - unused; stops its services
#   * boot to the console  - no desktop (a no-op on "Raspberry Pi OS Lite")
#   * compressed swap (zram) + swappiness - lets the model borrow a little extra headroom without wearing the SD card
#   * stops unused background services (ModemManager, triggerhappy)
# Wi-Fi/SSH, the web page, the printer and the GPIO button are NOT touched.
set -euo pipefail

BOOT_CFG="${JIAB_BOOT_CONFIG:-}"
if [[ -z "$BOOT_CFG" ]]; then
  for f in /boot/firmware/config.txt /boot/config.txt; do [[ -f "$f" ]] && { BOOT_CFG="$f"; break; }; done
fi
[[ -n "$BOOT_CFG" && -f "$BOOT_CFG" ]] || { echo "Can't find the Pi's config.txt (is this a Raspberry Pi?)"; exit 1; }
STATE_DIR="${JIAB_STATE_DIR:-/var/lib/jack-in-a-box}"
STATE="$STATE_DIR/lean-state"
DRY="${JIAB_DRY_RUN:-}"
SUDO=""; [[ -z "$DRY" && $EUID -ne 0 ]] && SUDO="sudo"
UNITS=(bluetooth hciuart triggerhappy ModemManager)

run() { if [[ -n "$DRY" ]]; then echo "   (dry run) $*"; else $SUDO "$@"; fi; }
set_kv() {   # set_kv file key value : replace an existing 'key=' line, else append
  if grep -qE "^[[:space:]]*$2=" "$1"; then $SUDO sed -i -E "s|^[[:space:]]*$2=.*|$2=$3|" "$1"
  else printf '%s=%s\n' "$2" "$3" | $SUDO tee -a "$1" >/dev/null; fi
}
add_line() { grep -qxF "$2" "$1" || printf '%s\n' "$2" | $SUDO tee -a "$1" >/dev/null; }

if [[ "${1:-}" == "--undo" ]]; then
  echo "==> Undoing lean mode"
  if [[ -f "$BOOT_CFG.jiab-backup" ]]; then $SUDO cp -p "$BOOT_CFG.jiab-backup" "$BOOT_CFG"; echo "   restored $BOOT_CFG"; fi
  if [[ -f "$STATE" ]]; then
    while IFS='=' read -r k v; do
      case "$k" in
        target)  run systemctl set-default "$v" ;;
        service) run systemctl enable --now "$v" || true ;;
      esac
    done < "$STATE"
    $SUDO rm -f "$STATE"
  fi
  $SUDO rm -f /etc/sysctl.d/99-jack-in-a-box.conf /etc/default/zramswap.jiab 2>/dev/null || true
  echo "Done. Reboot to finish:  sudo reboot"
  exit 0
fi

echo "==> Lean mode: freeing RAM for the local AI"
[[ -f "$BOOT_CFG.jiab-backup" ]] || $SUDO cp -p "$BOOT_CFG" "$BOOT_CFG.jiab-backup"
set_kv "$BOOT_CFG" gpu_mem 16
add_line "$BOOT_CFG" "dtoverlay=disable-bt"
echo "   $BOOT_CFG: gpu_mem=16, Bluetooth off (backup: $BOOT_CFG.jiab-backup)"

$SUDO mkdir -p "$STATE_DIR"
: > /tmp/jiab-lean-state.$$
if [[ -z "$DRY" ]]; then
  echo "target=$(systemctl get-default)" >> /tmp/jiab-lean-state.$$
  for u in "${UNITS[@]}"; do
    if systemctl is-enabled --quiet "$u.service" 2>/dev/null; then
      echo "service=$u.service" >> /tmp/jiab-lean-state.$$
      run systemctl disable --now "$u.service" || true
    fi
  done
  run systemctl set-default multi-user.target
else
  echo "target=graphical.target" >> /tmp/jiab-lean-state.$$
  echo "   (dry run) would stop: ${UNITS[*]}"
fi
$SUDO install -m 644 /tmp/jiab-lean-state.$$ "$STATE"; rm -f /tmp/jiab-lean-state.$$

echo "==> Compressed swap (zram)"
if [[ -z "$DRY" ]]; then
  run apt-get install -y -qq zram-tools
  printf 'ALGO=lz4\nPERCENT=100\nPERCENTAGE=100\nPRIORITY=100\n' | $SUDO tee /etc/default/zramswap >/dev/null
  printf 'vm.swappiness=100\nvm.vfs_cache_pressure=200\n' | $SUDO tee /etc/sysctl.d/99-jack-in-a-box.conf >/dev/null
  run systemctl restart zramswap || true
  $SUDO sysctl -q --system || true
else
  echo "   (dry run) would install zram-tools and set vm.swappiness=100"
fi

echo
echo "Done. Reboot to apply:  sudo reboot"
echo "Afterwards check free memory with:  free -m     (a Lite 3B+ should show ~800 MB available)"
echo "To go back:  bash deploy/lean-mode.sh --undo"
