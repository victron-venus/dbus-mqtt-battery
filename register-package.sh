#!/bin/bash
#
# Register dbus-mqtt-battery with SetupHelper PackageManager
# Run this on Cerbo after initial installation
#
# Usage: ./register-package.sh
#

PACKAGE_NAME="dbus-mqtt-battery"
PACKAGE_DIR="/data/$PACKAGE_NAME"
PM_DIR="/data/packageManager/$PACKAGE_NAME"

echo "Registering $PACKAGE_NAME with PackageManager..."

# Check if package is installed
if [ ! -d "$PACKAGE_DIR" ]; then
    echo "Error: Package not installed at $PACKAGE_DIR"
    echo "Run setup install first"
    exit 1
fi

# Create packageManager entry
mkdir -p "$PM_DIR"

# Copy gitHubInfo
if [ -f "$PACKAGE_DIR/gitHubInfo" ]; then
    cp "$PACKAGE_DIR/gitHubInfo" "$PM_DIR/"
else
    echo "victron-venus:latest" > "$PM_DIR/gitHubInfo"
fi

# Create symlink to current version
ln -sf "$PACKAGE_DIR" "$PM_DIR/current"

# Restart PackageManager to pick up new package
svc -t /service/PackageManager 2>/dev/null || true

echo "Done! Package registered."
echo ""
echo "You can now manage $PACKAGE_NAME via:"
echo "  - GUI v1: Settings → PackageManager"
echo "  - CLI:    /data/dbus-mqtt-battery/setup install"
