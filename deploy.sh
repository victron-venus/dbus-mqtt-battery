#!/bin/bash
#
# dbus-mqtt-battery deployment script
# Run this script on your local machine to deploy to a Venus OS device
#
# Usage: ./deploy.sh [Venus OS hostname or IP]
#
# Example: ./deploy.sh Cerbo
#

set -e

SSH_HOST="${1:-Cerbo}"

echo "=============================================="
echo "  dbus-mqtt-battery Deployment Script"
echo "=============================================="
echo "Target: $SSH_HOST"
echo ""

# Check if we can connect
echo "Checking connection to $SSH_HOST..."
ssh "$SSH_HOST" "echo 'Connected successfully'" || {
    echo "Failed to connect to $SSH_HOST"
    exit 1
}

echo ""
echo "Step 1: Creating backup of current installation..."
ssh "$SSH_HOST" '
    if [ -d "/data/dbus-mqtt-battery" ]; then
        TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
        cp -r /data/dbus-mqtt-battery "/data/dbus-mqtt-battery.backup.$TIMESTAMP"
        echo "Backup created: /data/dbus-mqtt-battery.backup.$TIMESTAMP"
    else
        echo "No existing installation to backup"
    fi
'

echo ""
echo "Step 2: Copying files to device..."
ssh "$SSH_HOST" "mkdir -p /data/dbus-mqtt-battery"
scp -q dbus-mqtt-battery.py "$SSH_HOST:/data/dbus-mqtt-battery/"
scp -q version "$SSH_HOST:/data/dbus-mqtt-battery/"
scp -q gitHubInfo "$SSH_HOST:/data/dbus-mqtt-battery/"
if [ -f "dvcc.py" ]; then
    scp -q dvcc.py "$SSH_HOST:/data/dbus-mqtt-battery/"
fi
echo "Files copied successfully"

echo ""
echo "Step 3: Running installation script..."
ssh "$SSH_HOST" "chmod +x /data/dbus-mqtt-battery/setup"
ssh "$SSH_HOST" "/data/dbus-mqtt-battery/setup install"

echo ""
echo "Step 4: Verifying installation..."
ssh "$SSH_HOST" '
    echo "Checking services:"
    svstat /service/dbus-mqtt-chain* 2>/dev/null || echo "No services found"
    echo ""
    echo "Checking version:"
    cat /data/dbus-mqtt-battery/version 2>/dev/null || echo "Version file not found"
'

echo ""
echo "Deployment completed successfully!"
echo ""
echo "To verify the installation, check:"
echo "  - PackageManager in Venus OS GUI"
echo "  - svstat /service/dbus-mqtt-chain*"
echo "  - Logs: svlogd /var/log/dbus-mqtt-chain*"
echo ""
echo "To rollback to previous version:"
echo "  ssh $SSH_HOST '/data/dbus-mqtt-battery/setup uninstall'"
echo "  ssh $SSH_HOST 'rm -rf /data/dbus-mqtt-battery'"
echo "  ssh $SSH_HOST 'mv /data/dbus-mqtt-battery.backup_* /data/dbus-mqtt-battery'"
echo "  ssh $SSH_HOST '/data/dbus-mqtt-battery/setup install'"
echo ""
