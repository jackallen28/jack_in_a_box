#!/usr/bin/env bash
# Trim the Pi's idle power draw.  Reversible:
#   bash deploy/eco-mode.sh           apply (then reboot)
#   bash deploy/eco-mode.sh --undo    put it back
#
#   * the green/red LEDs off                      (tiny saving, and no glowing box on the shelf)
#   * Bluetooth off                               (unused)
#   * HDMI output off at boot                     (best effort; harmless if there is no display)
#   * Wi-Fi power-save on                         (the radio naps between packets; the web page may answer ~100 ms slower)
# It does NOT touch USB (the printer and Ethernet share the Pi 3B+'s USB hub) or the CPU speed (the Pi already
# slows its CPU to 600 MHz when idle).  Savings are modest - a few hundred milliwatts - the biggest saver is
# idle power-off (Power & fan page) or a smart plug on a schedule.
set -euo pipefail

BOOT_CFG="${JIAB_BOOT_CONFIG:-}"
if [[ -z "$BOOT_CFG" ]]; then
  for f in /boot/firmware/config.txt /boot/config.txt; do [[ -f "$f" ]] && { BOOT_CFG="$f"; break; }; done
fi
[[ -n "$BOOT_CFG" && -f "$BOOT_CFG" ]] || { echo "Can't find the Pi's config.txt (is this a Raspberry Pi?)"; exit 1; }
DRY="${JIAB_DRY_RUN:-}"
SUDO=""; [[ -z "$DRY" && $EUID -ne 0 ]] && SUDO="sudo"
UNIT="${JIAB_UNIT_FILE:-/etc/systemd/system/jack-in-a-box-eco.service}"
BEGIN="# BEGIN jack-in-a-box eco"; END="# END jack-in-a-box eco"

strip_block() { $SUDO sed -i "/^$BEGIN\$/,/^$END\$/d" "$BOOT_CFG"; }

if [[ "${1:-}" == "--undo" ]]; then
  strip_block
  if [[ -z "$DRY" ]]; then
    $SUDO systemctl disable --now jack-in-a-box-eco.service 2>/dev/null || true
    $SUDO systemctl daemon-reload || true
  fi
  $SUDO rm -f "$UNIT"
  echo "Eco mode removed. Reboot to finish:  sudo reboot"
  exit 0
fi

strip_block                                                     # idempotent: rewrite our block each time
{
  echo "$BEGIN"
  echo "dtparam=act_led_trigger=none"
  echo "dtparam=pwr_led_trigger=none"
  grep -qxF "dtoverlay=disable-bt" "$BOOT_CFG" || echo "dtoverlay=disable-bt"
  echo "$END"
} | $SUDO tee -a "$BOOT_CFG" >/dev/null
echo "   $BOOT_CFG: LEDs off, Bluetooth off"

$SUDO mkdir -p "$(dirname "$UNIT")"
$SUDO tee "$UNIT" >/dev/null <<'UNITEOF'
[Unit]
Description=Jack in a Box - eco mode (HDMI off, Wi-Fi power save)

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'tvservice -o >/dev/null 2>&1 || vcgencmd display_power 0 >/dev/null 2>&1 || true; iw dev wlan0 set power_save on >/dev/null 2>&1 || true'

[Install]
WantedBy=multi-user.target
UNITEOF
if [[ -z "$DRY" ]]; then
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now jack-in-a-box-eco.service || true
else
  echo "   (dry run) would enable $UNIT"
fi
echo "Done. Reboot to apply the LED/Bluetooth changes:  sudo reboot"
echo "To go back:  bash deploy/eco-mode.sh --undo"
