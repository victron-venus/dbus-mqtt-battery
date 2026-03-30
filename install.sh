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
if [ -f "dbus-mqtt-battery.py" ]; then
    cp dbus-mqtt-battery.py "$INSTALL_DIR/"
    echo "Copied dbus-mqtt-battery.py"
fi
if [ -f "dbus-virtual-battery.py" ]; then
    cp dbus-virtual-battery.py "$INSTALL_DIR/"
    echo "Copied dbus-virtual-battery.py"
fi
chmod +x "$INSTALL_DIR"/*.py 2>/dev/null || true

# ============================================
# Chain 1: ESP32 #1 (topic: battery)
# ============================================
echo ""
echo ">>> Setting up Chain 1 service..."
mkdir -p /service/dbus-mqtt-chain1/log /var/log/dbus-mqtt-chain1
cat > /service/dbus-mqtt-chain1/run << EOF
#!/bin/sh
exec 2>/var/log/dbus-mqtt-chain1/stderr.log
cd $INSTALL_DIR
exec python3 dbus-mqtt-battery.py \\
    --broker $MQTT_BROKER \\
    --batteries 4 \\
    --instance 512 \\
    --topic-prefix battery \\
    --service-suffix mqtt_chain1 \\
    --product-name "JBD Battery Chain 1" \\
    --capacity 280
EOF
chmod +x /service/dbus-mqtt-chain1/run
cat > /service/dbus-mqtt-chain1/log/run << 'EOF'
#!/bin/sh
exec svlogd -tt /var/log/dbus-mqtt-chain1
EOF
chmod +x /service/dbus-mqtt-chain1/log/run
echo "Created /service/dbus-mqtt-chain1"

# ============================================
# Chain 2: ESP32 #2 (topic: battery2)
# ============================================
echo ""
echo ">>> Setting up Chain 2 service..."
mkdir -p /service/dbus-mqtt-chain2/log /var/log/dbus-mqtt-chain2
cat > /service/dbus-mqtt-chain2/run << EOF
#!/bin/sh
exec 2>/var/log/dbus-mqtt-chain2/stderr.log
cd $INSTALL_DIR
exec python3 dbus-mqtt-battery.py \\
    --broker $MQTT_BROKER \\
    --batteries 4 \\
    --instance 513 \\
    --topic-prefix battery2 \\
    --service-suffix mqtt_chain2 \\
    --product-name "JBD Battery Chain 2" \\
    --capacity 280
EOF
chmod +x /service/dbus-mqtt-chain2/run
cat > /service/dbus-mqtt-chain2/log/run << 'EOF'
#!/bin/sh
exec svlogd -tt /var/log/dbus-mqtt-chain2
EOF
chmod +x /service/dbus-mqtt-chain2/log/run
echo "Created /service/dbus-mqtt-chain2"

# ============================================
# Chain 3: Virtual (SmartShunt - Chain1 - Chain2)
# ============================================
# NOTE: SmartShunt D-Bus suffix (ttyUSB4) may vary depending on your system.
# To find your SmartShunt suffix, run: dbus -y | grep battery
# Example output: com.victronenergy.battery.ttyUSB4
# The suffix is the part after "battery." (e.g., ttyUSB4)
# If your SmartShunt has a different suffix, edit /service/dbus-virtual-chain/run after installation.

echo ""
echo ">>> Setting up Chain 3 (Virtual) service..."
mkdir -p /service/dbus-virtual-chain/log /var/log/dbus-virtual-chain
cat > /service/dbus-virtual-chain/run << 'EOF'
#!/bin/sh
exec 2>/var/log/dbus-virtual-chain/stderr.log
cd /data/apps/dbus-mqtt-battery
# SmartShunt suffix: change 'ttyUSB4' if your device has a different port
exec python3 dbus-virtual-battery.py \
    --smartshunt ttyUSB4 \
    --chains mqtt_chain1 mqtt_chain2 \
    --instance 514 \
    --product-name "JBD Battery Chain 3" \
    --capacity 280
EOF
chmod +x /service/dbus-virtual-chain/run
cat > /service/dbus-virtual-chain/log/run << 'EOF'
#!/bin/sh
exec svlogd -tt /var/log/dbus-virtual-chain
EOF
chmod +x /service/dbus-virtual-chain/log/run
echo "Created /service/dbus-virtual-chain"

# ============================================
# GUI v2 QML Files (Cell Voltages submenu)
# ============================================
echo ""
echo ">>> Installing GUI v2 QML files..."

# Check if overlay-fs is installed
if [ -d "/data/apps/overlay-fs" ]; then
    # Add overlay for GUI directory
    bash /data/apps/overlay-fs/add-app-and-directory.sh dbus-mqtt-battery /opt/victronenergy/gui 2>/dev/null || true
    
    # Copy QML files if available
    QML_SOURCE="$INSTALL_DIR/qml"
    QML_DEST="/opt/victronenergy/gui/qml"
    
    if [ -d "$QML_SOURCE" ]; then
        cp "$QML_SOURCE/PageBattery.qml" "$QML_DEST/" 2>/dev/null && echo "Installed PageBattery.qml"
        cp "$QML_SOURCE/PageBatteryDbusSerialbattery.qml" "$QML_DEST/" 2>/dev/null && echo "Installed PageBatteryDbusSerialbattery.qml"
        cp "$QML_SOURCE/PageBatteryDbusSerialbatteryCellVoltages.qml" "$QML_DEST/" 2>/dev/null && echo "Installed PageBatteryDbusSerialbatteryCellVoltages.qml"
        
        # Restart GUI to apply changes
        svc -d /service/gui && sleep 1 && svc -u /service/gui
        echo "GUI restarted to apply QML changes"
    else
        echo "QML source files not found in $QML_SOURCE"
        echo "Copy QML files manually to $QML_DEST if needed"
    fi
else
    echo "overlay-fs not installed. QML installation skipped."
    echo "Install overlay-fs first: https://github.com/kwindrem/SetupHelper"
fi

echo ""
echo "=============================================="
echo "  Installation Complete!"
echo "=============================================="
echo ""
echo "Services will start automatically."
echo ""
echo "Verify with:"
echo "  svstat /service/dbus-mqtt-chain1 /service/dbus-mqtt-chain2 /service/dbus-virtual-chain"
echo ""
echo "Commands:"
echo "  Status:   svstat /service/dbus-mqtt-chain1"
echo "  Restart:  svc -t /service/dbus-mqtt-chain1"
echo "  Stop:     svc -d /service/dbus-mqtt-chain1"
echo "  Logs:     cat /var/log/dbus-mqtt-chain1/stderr.log"
echo ""
echo "Note: Adjust SmartShunt suffix (ttyUSB4) in /service/dbus-virtual-chain/run if needed"
echo ""
echo "To find your SmartShunt suffix: dbus -y | grep battery"
