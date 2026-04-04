#!/bin/bash
#
# Deploy dbus-mqtt-battery to Venus OS via SetupHelper method
#
# Usage: ./deploy.sh
#
# This downloads the latest version from GitHub and runs setup install
#

set -e

SSH_HOST="${SSH_HOST:-Cerbo}"

echo "=============================================="
echo "  Deploying dbus-mqtt-battery to Venus OS"
echo "=============================================="
echo "SSH Host: $SSH_HOST"
echo ""

# Download and install
echo ">>> Downloading latest version..."
ssh "$SSH_HOST" 'rm -rf /data/dbus-mqtt-battery && \
cd /data && \
wget -qO - https://github.com/victron-venus/dbus-mqtt-battery/archive/latest.tar.gz | tar -xzf - && \
mv dbus-mqtt-battery-latest dbus-mqtt-battery && \
chmod +x /data/dbus-mqtt-battery/setup'

echo ">>> Running setup install..."
ssh "$SSH_HOST" '/data/dbus-mqtt-battery/setup install'

# Verify
echo ""
echo ">>> Waiting for services to start..."
sleep 8

echo ""
echo "=============================================="
echo "  Service Status"
echo "=============================================="
ssh "$SSH_HOST" 'svstat /service/dbus-mqtt-chain* /service/dbus-virtual-chain 2>/dev/null || echo "No services found"'

echo ""
echo "=============================================="
echo "  D-Bus Values"
echo "=============================================="
ssh "$SSH_HOST" 'for svc in dbus-mqtt-chain1 dbus-mqtt-chain2 virtual_chain; do
  name=$(dbus-send --system --print-reply --dest=com.victronenergy.battery.$svc /ProductName com.victronenergy.BusItem.GetValue 2>/dev/null | grep string | sed "s/.*\"\(.*\)\"/\1/")
  soc=$(dbus-send --system --print-reply --dest=com.victronenergy.battery.$svc /Soc com.victronenergy.BusItem.GetValue 2>/dev/null | grep -E "double|int32" | awk "{print \$NF}")
  current=$(dbus-send --system --print-reply --dest=com.victronenergy.battery.$svc /Dc/0/Current com.victronenergy.BusItem.GetValue 2>/dev/null | grep double | awk "{print \$NF}")
  if [ -n "$name" ]; then
    printf "%-25s SoC: %5s%%  Current: %6sA\n" "$name" "$soc" "$current"
  fi
done'

echo ""
echo "=============================================="
echo "  Deployment Complete!"
echo "=============================================="
echo ""
echo "Configuration: /data/setupOptions/dbus-mqtt-battery/"
echo "  chains       - Number of chains (default: 2)"
echo "  batteries    - Batteries per chain (default: 4)"
echo "  enableVirtual - Enable virtual battery (default: true)"
echo "  smartshunt   - SmartShunt port (default: ttyUSB0)"
echo ""
echo "Commands:"
echo "  Update:   ./deploy.sh"
echo "  Uninstall: ssh $SSH_HOST '/data/dbus-mqtt-battery/setup uninstall'"
echo "  Logs:      ssh $SSH_HOST 'tail -f /var/log/dbus-mqtt-chain1/current'"
