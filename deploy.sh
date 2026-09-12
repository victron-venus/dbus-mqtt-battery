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

SEPARATOR="=============================================="

echo "$SEPARATOR"
echo "  Deploying dbus-mqtt-battery to Venus OS"
echo "$SEPARATOR"
echo "SSH Host: $SSH_HOST"
echo ""

# Stop chain processes while retaining the supervisors and their directory inodes.
echo ">>> Stopping services..."
ssh "$SSH_HOST" 'for service in /service/dbus-mqtt-chain*; do
    [ ! -d "$service" ] || svc -d "$service"
done'

# Download and install
echo ">>> Downloading latest version..."
ssh "$SSH_HOST" 'sh -s -- victron-venus/dbus-mqtt-battery /data/dbus-mqtt-battery' <<'REMOTE_STAGE'
set -eu
repository=$1
destination=$2
package=${repository##*/}
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT HUP INT TERM
wget -qO "$staging/source.tar.gz" "https://github.com/$repository/archive/main.tar.gz"
tar -xzf "$staging/source.tar.gz" -C "$staging"
[ -f "$staging/$package-main/setup" ]
[ -f "$staging/$package-main/$package.py" ]
mkdir -p "$destination"
cp -R "$staging/$package-main/." "$destination/"
chmod +x "$destination/setup"
REMOTE_STAGE

echo ">>> Running setup install..."
ssh "$SSH_HOST" '/data/dbus-mqtt-battery/setup install'
ssh "$SSH_HOST" 'for service in /service/dbus-mqtt-chain*; do
    [ ! -d "$service" ] || svc -u "$service/log" "$service"
done'

# Restart PackageManager to discover package
echo ">>> Restarting PackageManager..."
ssh "$SSH_HOST" "svc -t /service/PackageManager 2>/dev/null || true"

# Wait for services to start
echo ""
echo ">>> Waiting for services to start..."
sleep 8

echo ""
echo "$SEPARATOR"
echo "  Service Status"
echo "$SEPARATOR"
ssh "$SSH_HOST" 'svstat /service/dbus-mqtt-chain* 2>/dev/null || echo "No services found"'

echo ""
echo "$SEPARATOR"
echo "  D-Bus Values"
echo "$SEPARATOR"
ssh "$SSH_HOST" 'for svc in dbus-mqtt-chain1 dbus-mqtt-chain2; do
  # NOTE: no "timeout" binary on Venus OS busybox - call dbus-send directly
  name=$(dbus-send --system --print-reply --dest=com.victronenergy.battery.$svc /ProductName com.victronenergy.BusItem.GetValue 2>/dev/null | grep variant | sed "s/.*string //; s/\"//g")
  soc=$(dbus-send --system --print-reply --dest=com.victronenergy.battery.$svc /Soc com.victronenergy.BusItem.GetValue 2>/dev/null | grep variant | awk "{print \$NF}")
  current=$(dbus-send --system --print-reply --dest=com.victronenergy.battery.$svc /Dc/0/Current com.victronenergy.BusItem.GetValue 2>/dev/null | grep variant | awk "{print \$NF}")
  if [ -n "$name" ]; then
    printf "%-25s SoC: %5s%%  Current: %6sA\n" "$name" "$soc" "$current"
  fi
done'

echo ""
echo "$SEPARATOR"
echo "  Deployment Complete!"
echo "$SEPARATOR"
echo ""
echo "Configuration: /data/setupOptions/dbus-mqtt-battery/"
echo "  chains       - Number of chains (default: 2)"
echo "  batteries    - Batteries per chain (default: 4)"
echo ""
echo "Commands:"
echo "  Update:   ./deploy.sh"
echo "  Uninstall: ssh $SSH_HOST '/data/dbus-mqtt-battery/setup uninstall'"
echo "  Logs:      ssh $SSH_HOST 'tail -f /var/log/dbus-mqtt-chain1/current'"
