#!/usr/bin/env bash
# Installs the app and makes it start automatically every time the Pi boots.
# Run from the project folder:   bash deploy/install.sh        (safe to re-run to update)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
[[ "$RUN_USER" == "root" ]] && { echo "Run as your normal user (not root); it uses sudo where needed."; exit 1; }

echo "==> Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pil python3-pip fonts-dejavu-core libusb-1.0-0 python3-gpiozero
# GPIO backend for the button: lgpio on current Raspberry Pi OS, RPi.GPIO on older releases
sudo apt-get install -y -qq python3-lgpio 2>/dev/null || sudo apt-get install -y -qq python3-rpi.gpio

echo "==> Creating Python environment in $DIR/.venv"
# --system-site-packages reuses the apt-built Pillow, which avoids compiling on the Pi
python3 -m venv --system-site-packages "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pip
"$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
mkdir -p "$DIR/data"

echo "==> GitHub access (so the app can update itself)"
# shellcheck source=github-auth.sh
source "$DIR/deploy/github-auth.sh"
if REPO_ROOT="$(git -C "$DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  setup_github_auth "$REPO_ROOT" "$DIR/data/.git-credentials" && AUTO_UPDATE=yes || AUTO_UPDATE=no
else
  echo "   Not a git clone (installed from a ZIP?) - auto-update unavailable."; AUTO_UPDATE=no
fi

echo "==> Letting '$RUN_USER' use the USB printer (group 'lp') and the button/LED pins (group 'gpio')"
sudo usermod -aG lp,gpio "$RUN_USER"
sudo cp "$DIR/deploy/99-epson-printer.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger

echo "==> Installing the start-on-boot service"
sed -e "s|@USER@|$RUN_USER|g" -e "s|@DIR@|$DIR|g" "$DIR/deploy/jack-in-a-box.service" | \
  sudo tee /etc/systemd/system/jack-in-a-box.service >/dev/null
for unit in jack-in-a-box-update.service jack-in-a-box-update.timer; do
  sed -e "s|@USER@|$RUN_USER|g" -e "s|@DIR@|$DIR|g" "$DIR/deploy/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
chmod +x "$DIR/deploy/update.sh"

# The app runs as YOU (not root). Its only extra powers are these exact commands (restart itself, run the updater,
# and power the Pi off when you enable "idle power-off"):
SYSTEMCTL="$(readlink -f "$(command -v systemctl)")"
SUDOERS_TMP="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: %s restart jack-in-a-box.service, %s start --no-block jack-in-a-box-update.service, %s poweroff\n' \
  "$RUN_USER" "$SYSTEMCTL" "$SYSTEMCTL" "$SYSTEMCTL" > "$SUDOERS_TMP"
if sudo visudo -cf "$SUDOERS_TMP" >/dev/null; then
  sudo install -m 440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/jack-in-a-box
else
  echo "   !! couldn't validate the sudo rule; auto-update restarts will not work"
fi
rm -f "$SUDOERS_TMP"

sudo systemctl daemon-reload
sudo systemctl enable jack-in-a-box.service      # start at every boot
sudo systemctl restart jack-in-a-box.service     # and start now
if [[ "$AUTO_UPDATE" == "yes" ]]; then sudo systemctl enable --now jack-in-a-box-update.timer
else sudo systemctl disable --now jack-in-a-box-update.timer >/dev/null 2>&1 || true; fi

sleep 3
if systemctl is-active --quiet jack-in-a-box.service; then STATE="running"; else STATE="NOT running - see: journalctl -u jack-in-a-box -n 50"; fi
IP="$(hostname -I | awk '{print $1}')"
echo
echo "Service: $STATE   |   starts on boot: $(systemctl is-enabled jack-in-a-box.service)   |   auto-update: $AUTO_UPDATE (hourly)"
echo "Open  http://$IP:8080   (or http://$(hostname).local:8080)  and choose a password."
echo
echo "Printer by USB:  plug it in and power it on (nothing else to do)."
echo "Button + LED:    wire it up (README), then Messages tab -> turn on \"Use a physical button + LED\"."
echo "(Only for a printer WITH a network card:  bash $DIR/deploy/printer-ethernet.sh)"
echo
echo "Optional: give the Pi a friendly name so you can use http://jack-in-a-box.local:8080 :"
echo "    sudo hostnamectl set-hostname jack-in-a-box && sudo reboot"
