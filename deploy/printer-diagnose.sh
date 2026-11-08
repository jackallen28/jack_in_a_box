#!/usr/bin/env bash
# Why can't the Pi talk to the Ethernet printer?  Prints a short report; paste it to get help.  Changes nothing.
#
#   bash ~/jack_in_a_box/deploy/printer-diagnose.sh                 # looks for the printer at its default 192.168.192.168
#   bash ~/jack_in_a_box/deploy/printer-diagnose.sh 192.168.1.50    # or at another address
HOST="${1:-}"; IFACE="${IFACE:-eth0}"
CONF="$HOME/jack_in_a_box/data/config.json"
if [[ -z "$HOST" && -f "$CONF" ]]; then HOST="$(python3 -c "import json;print(json.load(open('$CONF')).get('printer',{}).get('host',''))" 2>/dev/null || true)"; fi
HOST="${HOST:-192.168.192.168}"
say() { printf '\n== %s\n' "$*"; }

say "1. The Pi's Ethernet port ($IFACE)"
if [[ -e /sys/class/net/$IFACE ]]; then
  echo "state: $(cat /sys/class/net/$IFACE/operstate 2>/dev/null)   cable link: $([[ "$(cat /sys/class/net/$IFACE/carrier 2>/dev/null)" == 1 ]] && echo YES || echo "NO (nothing alive at the other end)")"
  ip -4 -o addr show dev "$IFACE" | awk '{print "address: "$4}' | grep . || echo "address: NONE (the Ethernet port has no IP: run printer-ethernet.sh)"
else
  echo "There is no $IFACE on this machine."
fi

say "2. Other networks (Wi-Fi etc.): the printer's range must not clash with these"
ip -4 -o addr show | awk '$2!="lo" {print $2": "$4}'

say "3. NetworkManager connections for $IFACE"
if command -v nmcli >/dev/null 2>&1; then nmcli -t -f NAME,DEVICE,TYPE,STATE connection show | grep -E "ethernet|$IFACE" || echo "(none)"; else echo "(NetworkManager isn't used here: dhcpcd)"; grep -A4 "BEGIN jack-in-a-box" /etc/dhcpcd.conf 2>/dev/null || true; fi

say "4. Printer at $HOST"
if ping -c 2 -W 2 "$HOST" >/dev/null 2>&1; then echo "ping: replies"; else echo "ping: no reply"; fi
if timeout 3 bash -c "exec 3<>/dev/tcp/$HOST/9100" 2>/dev/null; then echo "port 9100 (raw printing): OPEN"; else echo "port 9100 (raw printing): no answer"; fi

say "5. What the app is set to"
if [[ -f "$CONF" ]]; then python3 -c "import json;p=json.load(open('$CONF')).get('printer',{});print('connection:',p.get('backend'),' network IP:',p.get('host') or '(blank)',' port:',p.get('port'))"; else echo "no config yet"; fi

say "What it means"
carrier="$(cat /sys/class/net/$IFACE/carrier 2>/dev/null || echo 0)"
if [[ "$carrier" != 1 ]]; then echo "-> No cable link. Reseat the cable at both ends, try another cable, switch the printer on, wait a minute."
elif ! ip -4 -o addr show dev "$IFACE" | grep -q "inet "; then echo "-> The cable is fine but the Pi's Ethernet port has no address. Run:  bash ~/jack_in_a_box/deploy/printer-ethernet.sh"
elif timeout 3 bash -c "exec 3<>/dev/tcp/$HOST/9100" 2>/dev/null; then echo "-> The printer answers. In the web page: Printer -> Connection = Network, Network printer IP = $HOST, Save, Check connection."
elif ping -c 1 -W 2 "$HOST" >/dev/null 2>&1; then echo "-> $HOST answers ping but not on port 9100: wrong device, or raw printing is off on the printer's network card."
else echo "-> The Pi is set up and the cable is live, but nothing is at $HOST. Print the printer's self-test sheet (hold FEED while switching it on) and use the IP it shows (give it a minute: with no DHCP an Epson card falls back to 192.168.192.168)."; fi
