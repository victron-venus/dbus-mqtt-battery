# dbus-mqtt-battery

[![CI](https://github.com/victron-venus/dbus-mqtt-battery/actions/workflows/ci.yml/badge.svg)](https://github.com/victron-venus/dbus-mqtt-battery/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Release](https://img.shields.io/github/v/release/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/releases)
[![Downloads](https://img.shields.io/github/downloads/victron-venus/dbus-mqtt-battery/total)](https://github.com/victron-venus/dbus-mqtt-battery/releases)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Venus OS](https://img.shields.io/badge/Venus%20OS-3.x-blue)](https://github.com/victronenergy/venus)
[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)](https://github.com/victron-venus/dbus-mqtt-battery)
[![GitHub stars](https://img.shields.io/github/stars/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/network/members)
[![GitHub watchers](https://img.shields.io/github/watchers/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/watchers)
[![GitHub contributors](https://img.shields.io/github/contributors/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/graphs/contributors)
[![GitHub issues](https://img.shields.io/github/issues/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/issues)
[![GitHub closed issues](https://img.shields.io/github/issues-closed/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/issues?q=is%3Aissue+is%3Aclosed)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/pulls)
[![GitHub last commit](https://img.shields.io/github/last-commit/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/commits/main)
[![Code size](https://img.shields.io/github/languages/code-size/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery)
[![Repo size](https://img.shields.io/github/repo-size/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/victron-venus/dbus-mqtt-battery/graphs/commit-activity)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/victron-venus/dbus-mqtt-battery/pulls)
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)
[![Victron Community](https://img.shields.io/badge/Victron-Community-blue)](https://community.victronenergy.com/)

MQTT to D-Bus bridge for JBD BMS batteries via ESP32, plus virtual battery calculation.

> **Note**: This project requires [esphome-jbd-bms-mqtt](https://github.com/victron-venus/esphome-jbd-bms-mqtt) running on ESP32 to read BMS data via Bluetooth and publish to MQTT.

## System Architecture

```
                                     ┌─────────────────────────────────────┐
                                     │         Venus OS (Cerbo GX)         │
                                     │                                     │
 [Chain 1: 4x JBD BMS]               │  ┌─────────────────────────────┐    │
        │                            │  │    dbus-mqtt-battery.py     │    │
        │ BLE                        │  │    (--topic-prefix battery) │    │
        ▼                            │  │    → mqtt_chain1            │    │
 [ESP32 #1] ───MQTT───────────────────▶ └─────────────────────────────┘    │
 topic: battery                      │                │                    │
                                     │                ▼                    │
 [Chain 2: 4x JBD BMS]               │  ┌─────────────────────────────┐    │
        │                            │  │    dbus-mqtt-battery.py     │    │
        │ BLE                        │  │    (--topic-prefix battery2)│    │
        ▼                            │  │    → mqtt_chain2            │    │
 [ESP32 #2] ───MQTT───────────────────▶ └─────────────────────────────┘    │
 topic: battery2                     │                │                    │
                                     │                ▼                    │
 [Chain 3: 4x Batteries NO BMS]      │  ┌─────────────────────────────┐    │
        │                            │  │   dbus-virtual-battery.py   │    │
        │ Shunt                      │  │   SmartShunt - Chain1 - 2   │    │
        ▼                            │  │    → virtual_chain          │    │
 [SmartShunt] ──────VE.Direct─────────▶ └─────────────────────────────┘    │
                                     │                │                    │
                                     │                ▼                    │
                                     │        ┌─────────────┐              │
                                     │        │   D-Bus     │              │
                                     │        │             │              │
                                     │        │ mqtt_chain1 │              │
                                     │        │ mqtt_chain2 │              │
                                     │        │virtual_chain│              │
                                     │        └──────┬──────┘              │
                                     │               │                     │
                                     │               ▼                     │
                                     │        ┌─────────────┐              │
                                     │        │  Victron    │              │
                                     │        │  GUI v2     │              │
                                     │        └─────────────┘              │
                                     └─────────────────────────────────────┘
```

## Services

| Service | D-Bus Name | Source | Description |
|---------|------------|--------|-------------|
| Chain 1 | `com.victronenergy.battery.mqtt_chain1` | ESP32 #1 (MQTT `battery/`) | 4 JBD BMS batteries |
| Chain 2 | `com.victronenergy.battery.mqtt_chain2` | ESP32 #2 (MQTT `battery2/`) | 4 JBD BMS batteries |
| Chain 3 | `com.victronenergy.battery.virtual_chain` | Calculated | SmartShunt - Chain1 - Chain2 |

## Files

| File | Description |
|------|-------------|
| `dbus-mqtt-battery.py` | MQTT to D-Bus bridge for ESP32 |
| `dbus-virtual-battery.py` | Virtual battery calculator (no physical BMS) |
| `deploy-all.sh` | Deploy all 3 services at once |
| `deploy.sh` | Deploy single chain |
| `install.sh` | Install script (run on Venus OS) |

## Installation

### Option 1: SetupHelper (Recommended)

The easiest way to install is via [SetupHelper](https://github.com/kwindrem/SetupHelper) PackageManager:

1. **Install SetupHelper** (if not already installed):
   ```bash
   wget -qO - https://github.com/kwindrem/SetupHelper/archive/latest.tar.gz | tar -xzf - -C /data
   mv /data/SetupHelper-latest /data/SetupHelper
   /data/SetupHelper/setup
   ```

2. **Add package via GUI**:
   - Settings → PackageManager → Inactive packages → **new**
   - Package name: `dbus-mqtt-battery`
   - GitHub user: `victron-venus`
   - Branch/tag: `latest`
   - Proceed → Download → Install

3. **Done!** The package will:
   - Automatically reinstall after Venus OS updates
   - Update from GitHub when new versions are available
   - Provide GUI controls via PackageManager

### Option 2: Manual Deploy (All 3 Chains)

```bash
cd ~/victron/dbus-mqtt-battery
chmod +x deploy-all.sh
./deploy-all.sh <MQTT_BROKER_IP>
```

This deploys:
- **Chain 1**: `dbus-mqtt-chain1` service (ESP32 #1, topic `battery`)
- **Chain 2**: `dbus-mqtt-chain2` service (ESP32 #2, topic `battery2`)
- **Chain 3**: `dbus-virtual-chain` service (SmartShunt - Chain1 - Chain2)

## Individual Chain Deployment

### Chain 1 (ESP32 #1)

```bash
# Flash ESP32 #1
cd esphome
esphome run jbd-all-batteries1.yaml

# Deploy service
ssh Cerbo 'mkdir -p /data/apps/dbus-mqtt-battery /service/dbus-mqtt-chain1/log /var/log/dbus-mqtt-chain1'
scp dbus-mqtt-battery.py Cerbo:/data/apps/dbus-mqtt-battery/
ssh Cerbo 'chmod +x /data/apps/dbus-mqtt-battery/dbus-mqtt-battery.py && \
cat > /service/dbus-mqtt-chain1/run << "EOF"
#!/bin/sh
exec 2>&1
cd /data/apps/dbus-mqtt-battery
exec python3 dbus-mqtt-battery.py \
    --broker <MQTT_BROKER_IP> \
    --batteries 4 \
    --instance 512 \
    --topic-prefix battery \
    --service-suffix mqtt_chain1 \
    --product-name "JBD Battery Chain 1"
EOF
chmod +x /service/dbus-mqtt-chain1/run && \
cat > /service/dbus-mqtt-chain1/log/run << "LOGEOF"
#!/bin/sh
exec svlogd -tt /var/log/dbus-mqtt-chain1
LOGEOF
chmod +x /service/dbus-mqtt-chain1/log/run'
```

### Chain 2 (ESP32 #2)

```bash
# Flash ESP32 #2
cd esphome
esphome run jbd-all-batteries2.yaml

# Deploy service  
ssh Cerbo 'mkdir -p /service/dbus-mqtt-chain2/log /var/log/dbus-mqtt-chain2 && \
cat > /service/dbus-mqtt-chain2/run << "EOF"
#!/bin/sh
exec 2>&1
cd /data/apps/dbus-mqtt-battery
exec python3 dbus-mqtt-battery.py \
    --broker <MQTT_BROKER_IP> \
    --batteries 4 \
    --instance 513 \
    --topic-prefix battery2 \
    --service-suffix mqtt_chain2 \
    --product-name "JBD Battery Chain 2"
EOF
chmod +x /service/dbus-mqtt-chain2/run && \
cat > /service/dbus-mqtt-chain2/log/run << "LOGEOF"
#!/bin/sh
exec svlogd -tt /var/log/dbus-mqtt-chain2
LOGEOF
chmod +x /service/dbus-mqtt-chain2/log/run'
```

### Chain 3 (Virtual - No BMS)

```bash
# Deploy virtual battery service
scp dbus-virtual-battery.py Cerbo:/data/apps/dbus-mqtt-battery/
ssh Cerbo 'chmod +x /data/apps/dbus-mqtt-battery/dbus-virtual-battery.py && \
mkdir -p /service/dbus-virtual-chain/log /var/log/dbus-virtual-chain && \
cat > /service/dbus-virtual-chain/run << "EOF"
#!/bin/sh
exec 2>&1
cd /data/apps/dbus-mqtt-battery
exec python3 dbus-virtual-battery.py \
    --smartshunt ttyUSB4 \
    --chains mqtt_chain1 mqtt_chain2 \
    --instance 514 \
    --product-name "JBD Battery Chain 3" \
    --capacity 280
EOF
chmod +x /service/dbus-virtual-chain/run && \
cat > /service/dbus-virtual-chain/log/run << "LOGEOF"
#!/bin/sh
exec svlogd -tt /var/log/dbus-virtual-chain
LOGEOF
chmod +x /service/dbus-virtual-chain/log/run'
```

## ESP32 Configuration

### ESP32 #1 (Chain 1)
- ESPHome config: `esphome/jbd-all-batteries1.yaml`
- MQTT topic prefix: `battery`
- BMS MAC addresses: configured in YAML

### ESP32 #2 (Chain 2)
- ESPHome config: `esphome/jbd-all-batteries2.yaml`
- MQTT topic prefix: `battery2`
- BMS MAC addresses: configured in YAML

## MQTT Topics

### Chain 1 (topic_prefix: battery)
```
battery/sensor/voltage_bms1/state
battery/sensor/current_bms1/state
battery/sensor/soc_bms1/state
battery/sensor/voltage_total/state
...
```

### Chain 2 (topic_prefix: battery2)
```
battery2/sensor/voltage_bms1/state
battery2/sensor/current_bms1/state
battery2/sensor/soc_bms1/state
battery2/sensor/voltage_total/state
...
```

## Command Line Arguments

### dbus-mqtt-battery.py

| Argument | Default | Description |
|----------|---------|-------------|
| `--broker` | `<MQTT_BROKER_IP>` | MQTT broker address |
| `--port` | `1883` | MQTT broker port |
| `--batteries` | `4` | Number of batteries in this chain |
| `--bms-first` | `1` | First MQTT BMS index for this chain (see multi-chain below) |
| `--instance` | `512` | D-Bus device instance |
| `--topic-prefix` | `battery` | MQTT topic prefix |
| `--service-suffix` | `mqtt_chain` | D-Bus service suffix |
| `--product-name` | `JBD Battery Chain` | Product name in GUI |
| `--capacity` | `280` | Installed Ah (series string) |

**Multi-chain from one ESP (topics `battery/sensor/..._bms1` … `_bms4`):** run two services with different `--service-suffix` and BMS ranges, for example:

- Chain 1: `--batteries 2 --bms-first 1 --service-suffix mqtt_chain1` → uses `bms1`, `bms2`
- Chain 2: `--batteries 2 --bms-first 3 --service-suffix mqtt_chain2` → uses `bms3`, `bms4`

**`voltage_total` without `current_total`:** if the GUI showed **0 A** while per-BMS MQTT had real current, the script was using stale `current_total=0`. v2.6+ uses per-BMS current when `current_total` was never published.

### dbus-virtual-battery.py

| Argument | Default | Description |
|----------|---------|-------------|
| `--smartshunt` | `ttyUSB4` | SmartShunt D-Bus suffix |
| `--chains` | `mqtt_chain1 mqtt_chain2` | Chains to subtract |
| `--instance` | `514` | D-Bus device instance |
| `--product-name` | `JBD Battery Chain 3` | Product name in GUI |
| `--capacity` | `280` | Chain capacity in Ah |

## Service Management (on Venus OS)

```bash
# Check all services
svstat /service/dbus-mqtt-chain1 /service/dbus-mqtt-chain2 /service/dbus-virtual-chain

# Restart specific service
svc -t /service/dbus-mqtt-chain1

# Stop service
svc -d /service/dbus-mqtt-chain1

# Start service
svc -u /service/dbus-mqtt-chain1

# View logs
tail -f /var/log/dbus-mqtt-chain1/current
tail -f /var/log/dbus-mqtt-chain2/current
tail -f /var/log/dbus-virtual-chain/current
```

## Verify D-Bus Values

```bash
ssh Cerbo 'echo "=== Chain 1 ===" && \
dbus -y com.victronenergy.battery.mqtt_chain1 /Dc/0/Voltage GetValue && \
dbus -y com.victronenergy.battery.mqtt_chain1 /Dc/0/Current GetValue && \
dbus -y com.victronenergy.battery.mqtt_chain1 /Soc GetValue && \
echo "=== Chain 2 ===" && \
dbus -y com.victronenergy.battery.mqtt_chain2 /Dc/0/Voltage GetValue && \
dbus -y com.victronenergy.battery.mqtt_chain2 /Dc/0/Current GetValue && \
dbus -y com.victronenergy.battery.mqtt_chain2 /Soc GetValue && \
echo "=== Chain 3 (Virtual) ===" && \
dbus -y com.victronenergy.battery.virtual_chain /Dc/0/Voltage GetValue && \
dbus -y com.victronenergy.battery.virtual_chain /Dc/0/Current GetValue && \
dbus -y com.victronenergy.battery.virtual_chain /Soc GetValue && \
echo "=== SmartShunt (Total) ===" && \
dbus -y com.victronenergy.battery.ttyUSB4 /Dc/0/Voltage GetValue && \
dbus -y com.victronenergy.battery.ttyUSB4 /Dc/0/Current GetValue'
```

## Uninstall

```bash
ssh Cerbo 'svc -d /service/dbus-mqtt-chain1 /service/dbus-mqtt-chain2 /service/dbus-virtual-chain
rm -rf /service/dbus-mqtt-chain1 /service/dbus-mqtt-chain2 /service/dbus-virtual-chain
rm -rf /data/apps/dbus-mqtt-battery
rm -rf /var/log/dbus-mqtt-chain1 /var/log/dbus-mqtt-chain2 /var/log/dbus-virtual-chain'
```

## D-Bus Paths (per chain)

| Path | Description |
|------|-------------|
| /Dc/0/Voltage | Total battery voltage (V) |
| /Dc/0/Current | Total battery current (A) |
| /Dc/0/Power | Total power (W) |
| /Soc | State of charge (%) |
| /Capacity | Total capacity (Ah) |
| /System/MinCellVoltage | Minimum cell voltage (V) |
| /System/MaxCellVoltage | Maximum cell voltage (V) |
| /System/NrOfModulesOnline | Online battery count |
| /Io/AllowToCharge | Charge FET status |
| /Io/AllowToDischarge | Discharge FET status |

## Troubleshooting

### Chain 2 shows N/A
ESP32 #2 needs to be flashed and connected to WiFi/MQTT.

### Virtual chain current is wrong
Verify SmartShunt suffix:
```bash
dbus -y | grep battery
# Example output: com.victronenergy.battery.ttyUSB4
# The suffix is the part after "battery." (e.g., ttyUSB4)
```
If your SmartShunt has a different suffix (e.g., `ttyUSB0`), edit:
```bash
vi /service/dbus-virtual-chain/run
# Change --smartshunt ttyUSB4 to your actual suffix
svc -t /service/dbus-virtual-chain  # Restart service
```

### Service keeps restarting
Run manually to see errors:
```bash
svc -d /service/dbus-mqtt-chain1
cd /data/apps/dbus-mqtt-battery
python3 dbus-mqtt-battery.py --broker <MQTT_BROKER_IP> --topic-prefix battery --service-suffix mqtt_chain1
```

## Related Projects

This project is part of a Victron Venus OS integration suite:

| Project | Description |
|---------|-------------|
| [inverter-control](https://github.com/victron-venus/inverter-control) | ESS external control with web dashboard |
| [inverter-dashboard](https://github.com/victron-venus/inverter-dashboard) | Remote web dashboard via MQTT (Docker) |
| **dbus-mqtt-battery** (this) | MQTT to D-Bus bridge for BMS integration |
| [dbus-tasmota-pv](https://github.com/victron-venus/dbus-tasmota-pv) | Tasmota smart plug as PV inverter on D-Bus |
| [esphome-jbd-bms-mqtt](https://github.com/victron-venus/esphome-jbd-bms-mqtt) | ESP32 Bluetooth monitor for JBD BMS |

## License

MIT License
