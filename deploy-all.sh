#!/bin/bash
#
# Deploy all battery services to Venus OS
#
# This script deploys:
#   - Chain 1: dbus-mqtt-battery (from ESP32 #1, topic: battery)
#   - Chain 2: dbus-mqtt-battery (from ESP32 #2, topic: battery2)  
#   - Chain 3: dbus-virtual-battery (calculated from SmartShunt - Chain1 - Chain2)
#
# Prerequisites:
#   - SSH config with host 'r' pointing to Venus OS device
#   - ESP32 #1 flashed with jbd-all-batteries1.yaml (topic_prefix: battery)
#   - ESP32 #2 flashed with jbd-all-batteries2.yaml (topic_prefix: battery2)
#
# Usage: ./deploy-all.sh [MQTT_BROKER]
#

set -e

SSH_HOST="${SSH_HOST:-Cerbo}"
MQTT_BROKER="${1:-192.168.160.150}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=============================================="
echo "  Deploying Battery Services to Venus OS"
echo "=============================================="
echo "SSH Host: $SSH_HOST"
echo "MQTT Broker: $MQTT_BROKER"
echo ""

# Copy all Python scripts
echo ">>> Copying Python scripts..."
ssh "$SSH_HOST" "mkdir -p /data/apps/dbus-mqtt-battery"
scp "$SCRIPT_DIR/dbus-mqtt-battery.py" "$SSH_HOST:/data/apps/dbus-mqtt-battery/"
scp "$SCRIPT_DIR/dbus-virtual-battery.py" "$SSH_HOST:/data/apps/dbus-mqtt-battery/"
ssh "$SSH_HOST" "chmod +x /data/apps/dbus-mqtt-battery/*.py"

# ============================================
# Chain 1: ESP32 #1 (topic: battery)
# ============================================
echo ""
echo ">>> Setting up Chain 1 service (mqtt_chain1)..."
ssh "$SSH_HOST" "mkdir -p /service/dbus-mqtt-chain1/log /var/log/dbus-mqtt-chain1 && \
cat > /service/dbus-mqtt-chain1/run << 'RUNEOF'
#!/bin/sh
exec 2>&1
cd /data/apps/dbus-mqtt-battery
exec python3 dbus-mqtt-battery.py \\
    --broker $MQTT_BROKER \\
    --batteries 4 \\
    --instance 512 \\
    --topic-prefix battery \\
    --service-suffix mqtt_chain1 \\
    --product-name 'JBD Battery Chain 1'
RUNEOF
chmod +x /service/dbus-mqtt-chain1/run && \
cat > /service/dbus-mqtt-chain1/log/run << 'LOGEOF'
#!/bin/sh
exec svlogd -tt /var/log/dbus-mqtt-chain1
LOGEOF
chmod +x /service/dbus-mqtt-chain1/log/run"

# ============================================
# Chain 2: ESP32 #2 (topic: battery2)
# ============================================
echo ""
echo ">>> Setting up Chain 2 service (mqtt_chain2)..."
ssh "$SSH_HOST" "mkdir -p /service/dbus-mqtt-chain2/log /var/log/dbus-mqtt-chain2 && \
cat > /service/dbus-mqtt-chain2/run << 'RUNEOF'
#!/bin/sh
exec 2>&1
cd /data/apps/dbus-mqtt-battery
exec python3 dbus-mqtt-battery.py \\
    --broker $MQTT_BROKER \\
    --batteries 4 \\
    --instance 513 \\
    --topic-prefix battery2 \\
    --service-suffix mqtt_chain2 \\
    --product-name 'JBD Battery Chain 2'
RUNEOF
chmod +x /service/dbus-mqtt-chain2/run && \
cat > /service/dbus-mqtt-chain2/log/run << 'LOGEOF'
#!/bin/sh
exec svlogd -tt /var/log/dbus-mqtt-chain2
LOGEOF
chmod +x /service/dbus-mqtt-chain2/log/run"

# ============================================
# Chain 3: Virtual (SmartShunt - Chain1 - Chain2)
# ============================================
# NOTE: SmartShunt D-Bus suffix (ttyUSB4) may vary depending on your system.
# To find your SmartShunt suffix, run: ssh r 'dbus -y | grep battery'
# Example output: com.victronenergy.battery.ttyUSB4
# The suffix is the part after "battery." (e.g., ttyUSB4)
# If your SmartShunt has a different suffix, edit /service/dbus-virtual-chain/run after deployment.

echo ""
echo ">>> Setting up Chain 3 service (virtual_chain)..."
ssh "$SSH_HOST" "mkdir -p /service/dbus-virtual-chain/log /var/log/dbus-virtual-chain && \
cat > /service/dbus-virtual-chain/run << 'RUNEOF'
#!/bin/sh
exec 2>&1
cd /data/apps/dbus-mqtt-battery
# SmartShunt suffix: change 'ttyUSB4' if your device has a different port
exec python3 dbus-virtual-battery.py \\
    --smartshunt ttyUSB4 \\
    --chains mqtt_chain1 mqtt_chain2 \\
    --instance 514 \\
    --product-name 'JBD Battery Chain 3' \\
    --capacity 280
RUNEOF
chmod +x /service/dbus-virtual-chain/run && \
cat > /service/dbus-virtual-chain/log/run << 'LOGEOF'
#!/bin/sh
exec svlogd -tt /var/log/dbus-virtual-chain
LOGEOF
chmod +x /service/dbus-virtual-chain/log/run"

# Wait for services to start
echo ""
echo ">>> Waiting for services to start..."
sleep 10

# Verify
echo ""
echo "=============================================="
echo "  Service Status"
echo "=============================================="
ssh "$SSH_HOST" "svstat /service/dbus-mqtt-chain1 /service/dbus-mqtt-chain2 /service/dbus-virtual-chain"

echo ""
echo "=============================================="
echo "  D-Bus Values"
echo "=============================================="
ssh "$SSH_HOST" 'echo "=== Chain 1 ===" && \
echo "  Voltage: $(dbus -y com.victronenergy.battery.mqtt_chain1 /Dc/0/Voltage GetValue 2>/dev/null || echo N/A)" && \
echo "  Current: $(dbus -y com.victronenergy.battery.mqtt_chain1 /Dc/0/Current GetValue 2>/dev/null || echo N/A)" && \
echo "  SoC:     $(dbus -y com.victronenergy.battery.mqtt_chain1 /Soc GetValue 2>/dev/null || echo N/A)" && \
echo "" && \
echo "=== Chain 2 ===" && \
echo "  Voltage: $(dbus -y com.victronenergy.battery.mqtt_chain2 /Dc/0/Voltage GetValue 2>/dev/null || echo N/A)" && \
echo "  Current: $(dbus -y com.victronenergy.battery.mqtt_chain2 /Dc/0/Current GetValue 2>/dev/null || echo N/A)" && \
echo "  SoC:     $(dbus -y com.victronenergy.battery.mqtt_chain2 /Soc GetValue 2>/dev/null || echo N/A)" && \
echo "" && \
echo "=== Chain 3 (Virtual) ===" && \
echo "  Voltage: $(dbus -y com.victronenergy.battery.virtual_chain /Dc/0/Voltage GetValue 2>/dev/null || echo N/A)" && \
echo "  Current: $(dbus -y com.victronenergy.battery.virtual_chain /Dc/0/Current GetValue 2>/dev/null || echo N/A)" && \
echo "  SoC:     $(dbus -y com.victronenergy.battery.virtual_chain /Soc GetValue 2>/dev/null || echo N/A)" && \
echo "" && \
echo "=== SmartShunt (Total) ===" && \
echo "  Voltage: $(dbus -y com.victronenergy.battery.ttyUSB4 /Dc/0/Voltage GetValue 2>/dev/null || echo N/A)" && \
echo "  Current: $(dbus -y com.victronenergy.battery.ttyUSB4 /Dc/0/Current GetValue 2>/dev/null || echo N/A)" && \
echo "  SoC:     $(dbus -y com.victronenergy.battery.ttyUSB4 /Soc GetValue 2>/dev/null || echo N/A)"'

echo ""
echo "=============================================="
echo "  Deployment Complete!"
echo "=============================================="
echo ""
echo "Commands (on Venus OS):"
echo "  Status:   svstat /service/dbus-mqtt-chain1 /service/dbus-mqtt-chain2 /service/dbus-virtual-chain"
echo "  Logs:"
echo "    Chain 1: tail -f /var/log/dbus-mqtt-chain1/current"
echo "    Chain 2: tail -f /var/log/dbus-mqtt-chain2/current"
echo "    Chain 3: tail -f /var/log/dbus-virtual-chain/current"
echo ""
echo "Note: Chain 2 requires ESP32 #2 to be flashed and connected!"
