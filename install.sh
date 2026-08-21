#!/bin/bash
#
# dbus-mqtt-battery installer for Venus OS
# Run this script ON Venus OS after copying files
#
# Usage: ./install.sh [MQTT_BROKER]
#
# Example: ./install.sh 192.168.160.150
#

set -e

MQTT_BROKER="${1:-192.168.160.150}"

INSTALL_DIR="/data/apps/dbus-mqtt-battery"

echo "=============================================="
echo "  dbus-mqtt-battery Installer for Venus OS"
echo "=============================================="
echo "MQTT Broker: $MQTT_BROKER"
echo ""

# Create install directory
mkdir -p "$INSTALL_DIR"

# Copy Python scripts (if running from source directory)
if [[ -f "dbus-mqtt-battery.py" ]] ; then
    cp dbus-mqtt-battery.py "$INSTALL_DIR/"
    echo "Copied dbus-mqtt-battery.py"
fi

if [[ -f "dvcc.py" ]] ; then
    cp dvcc.py "$INSTALL_DIR/"
    echo "Copied dvcc.py"
fi

# Copy version file
if [[ -f "version" ]] ; then
    cp version "$INSTALL_DIR/"
    echo "Copied version file"
fi

# Copy gitHubInfo
if [[ -f "gitHubInfo" ]] ; then
    cp gitHubInfo "$INSTALL_DIR/"
    echo "Copied gitHubInfo"
fi

echo ""
echo "Installation complete. Files copied to: $INSTALL_DIR"
echo ""
echo "To complete installation via SetupHelper:"
echo "  1. Ensure /data/setupOptions/dbus-mqtt-battery/ exists with desired options"
echo "  2. Run: /data/SetupHelper/HelperScripts/installer.sh dbus-mqtt-battery"
echo ""
