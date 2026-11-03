#!/usr/bin/env bash
# Prepare the Pi's built-in Ethernet port to talk DIRECTLY to a network-capable printer
# (cable from the Pi's Ethernet port to the printer's Ethernet port), without touching Wi-Fi.
#
#   bash deploy/printer-ethernet.sh              # Pi gets 192.168.192.1/24 on eth0
#   bash deploy/printer-ethernet.sh 10.50.0.1    # or pick your own address
#
# Why Wi-Fi is unaffected: eth0 gets a fixed address on its own little subnet and is
# told never to be the default route, so internet / Gmail / your SSH + web page keep
# going over Wi-Fi (wlan0). Nothing else is changed.
set -euo pipefail

# Does a printer answer at HOST (raw printing port 9100)?   bash printer-ethernet.sh --check 192.168.192.168
probe() {
  local host="$1" port="${2:-9100}"
  if timeout 3 bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null; then
    echo "   OK  The printer answered at $host:$port."
    echo "       Now: web page -> Printer tab -> Connection = Network, Network printer IP = $host, Save, 'Check connection'."
    return 0
  fi
  if ping -c 1 -W 2 "$host" >/dev/null 2>&1; then
    echo "   !!  $host replies to ping but nothing is listening on port $port. Is it the right device / is raw printing enabled?"
  else
    echo "   !!  No answer from $host. Check: printer switched on, cable firmly in both ends (try another cable),"
    echo "       and that the printer's self-test page (hold FEED while powering on) shows IP $host."
  fi
  return 1
}
if [[ "${1:-}" == "--check" ]]; then probe "${2:?usage: $0 --check HOST [PORT]}" "${3:-9100}"; exit $?; fi

PI_IP="${1:-192.168.192.1}"
IFACE="${IFACE:-eth0}"
NET="${PI_IP%.*}"

# Refuse to create a subnet clash with Wi-Fi / any other interface (that WOULD break things).
for other in $(ip -o -4 addr show | awk '{print $2 ":" $4}'); do
  ifc="${other%%:*}"; addr="${other#*:}"
  [[ "$ifc" == "$IFACE" || "$ifc" == "lo" ]] && continue
  if [[ "${addr%.*}" == "$NET" ]]; then
    echo "!! $ifc is already on $NET.0/24 ($addr). Pick another range, e.g.:  bash $0 10.50.0.1"
    exit 1
  fi
done

if command -v nmcli >/dev/null 2>&1 && systemctl is-active --quiet NetworkManager; then
  echo "==> Using NetworkManager"
  sudo nmcli connection delete printer-link >/dev/null 2>&1 || true
  sudo nmcli connection add type ethernet ifname "$IFACE" con-name printer-link \
      ipv4.method manual ipv4.addresses "$PI_IP/24" ipv4.never-default yes \
      ipv6.method disabled connection.autoconnect yes >/dev/null
  # Stop the default "Wired connection 1" (DHCP) from fighting over eth0.
  for c in $(nmcli -t -f NAME,DEVICE connection show | awk -F: -v i="$IFACE" '$2==i && $1!="printer-link"{print $1}'); do
    sudo nmcli connection modify "$c" connection.autoconnect no || true
  done
  sudo nmcli connection up printer-link >/dev/null || true
else
  echo "==> Using dhcpcd"
  CONF=/etc/dhcpcd.conf
  sudo sed -i '/# BEGIN jack-in-a-box/,/# END jack-in-a-box/d' "$CONF"
  printf '%s\n' "# BEGIN jack-in-a-box" "interface $IFACE" "static ip_address=$PI_IP/24" "nogateway" "# END jack-in-a-box" | sudo tee -a "$CONF" >/dev/null
  # Apply to eth0 only (a full dhcpcd restart could briefly drop Wi-Fi/SSH).
  sudo ip addr replace "$PI_IP/24" dev "$IFACE"
  sudo ip link set "$IFACE" up
fi

echo
echo "Pi Ethernet ($IFACE) is now $PI_IP/24 with no default route; Wi-Fi is unchanged."
echo
if [[ "$PI_IP" == 192.168.192.* ]]; then
  echo "Checking for the printer at its default address 192.168.192.168 (waiting for the network link)..."
  sleep 6
  probe 192.168.192.168 9100 || true
fi
echo
echo "Next:"
echo "  1. Cable Pi Ethernet -> printer Ethernet, power the printer on."
echo "  2. Find the printer's IP: hold the FEED button while switching it on to print a self-test sheet."
echo "     Epson network cards with no DHCP answer fall back to 192.168.192.168, which is reachable from"
echo "     the default Pi address ($PI_IP)."
echo "  3. In the web page: Printer tab -> Connection = Network, enter that IP, Save, then 'Check connection'."
echo "  4. Quick test from the Pi:  ping -c2 <printer-ip>"
