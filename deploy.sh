#!/bin/bash
#
# Deploy dbus-mqtt-battery files to Venus OS from local machine
# Then run install.sh on Venus OS
#
# Prerequisites:
#   - SSH config with host 'Cerbo' pointing to Venus OS device
#   - SSH key authentication configured
#
# Usage: ./deploy.sh [MQTT_BROKER]
#
# This is a simplified version. For full 3-chain deployment, use deploy-all.sh
#
# Use case: ./deploy.sh && ssh Cerbo 'svc -t /service/dbus-mqtt-chain1 /service/dbus-mqtt-chain2 && sleep 3 && tail -15 /var/log/dbus-mqtt-chain1/stderr.log'

set -e

SSH_HOST="${SSH_HOST:-Cerbo}"
MQTT_BROKER="${1:-192.168.160.150}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=============================================="
echo "  Deploying dbus-mqtt-battery to Venus OS"
echo "=============================================="
echo "SSH Host: $SSH_HOST"
echo "MQTT Broker: $MQTT_BROKER"
echo ""

# Create directory on remote
echo ">>> Creating directory..."
ssh "$SSH_HOST" "mkdir -p /data/apps/dbus-mqtt-battery"

# Copy Python scripts
echo ">>> Copying Python scripts..."
scp "$SCRIPT_DIR/dbus-mqtt-battery.py" "$SSH_HOST:/data/apps/dbus-mqtt-battery/"
scp "$SCRIPT_DIR/dbus-virtual-battery.py" "$SSH_HOST:/data/apps/dbus-mqtt-battery/"
scp "$SCRIPT_DIR/install.sh" "$SSH_HOST:/data/apps/dbus-mqtt-battery/"

# Make executable
ssh "$SSH_HOST" "chmod +x /data/apps/dbus-mqtt-battery/*.py /data/apps/dbus-mqtt-battery/install.sh"

echo ""
echo "=============================================="
echo "  Files Copied Successfully"
echo "=============================================="
echo ""
echo "Next steps - SSH to Venus OS and run:"
echo "  ssh $SSH_HOST"
echo "  cd /data/apps/dbus-mqtt-battery"
echo "  ./install.sh $MQTT_BROKER"
echo ""
echo "Or run install remotely:"
echo "  ssh $SSH_HOST 'cd /data/apps/dbus-mqtt-battery && ./install.sh $MQTT_BROKER'"
echo ""
echo "For automated full deployment, use: ./deploy-all.sh"
