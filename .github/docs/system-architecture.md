# System Architecture

## Data Flow

```mermaid
flowchart TB
    subgraph Chained["JBD BMS Chains"]
        subgraph Chain1["Chain 1: 4x JBD BMS"]
            B1["BMS 1"]; B2["BMS 2"]; B3["BMS 3"]; B4["BMS 4"]
        end
        subgraph Chain2["Chain 2: 4x JBD BMS"]
            B5["BMS 5"]; B6["BMS 6"]; B7["BMS 7"]; B8["BMS 8"]
        end
    end

    subgraph ESP32["ESP32 BLE Proxies"]
        E1["ESP32 #1"]; E2["ESP32 #2"]
    end

    subgraph Venus["Venus OS"]
        MQTT["MQTT Broker"]
        DMB1["dbus-mqtt-battery\n(topic: battery)"]
        DMB2["dbus-mqtt-battery\n(topic: battery2)"]
        VBT["dbus-virtual-battery"]
        DBUS["D-Bus"]
        CALC["systemcalc-py"]
    end

    B1 & B2 & B3 & B4 -->|"BLE"| E1
    B5 & B6 & B7 & B8 -->|"BLE"| E2
    E1 -->|"battery/*"| MQTT; E2 -->|"battery2/*"| MQTT

    MQTT --> DMB1 -->|"mqtt_chain1"| DBUS
    MQTT --> DMB2 -->|"mqtt_chain2"| DBUS
    DBUS --> CALC

    style DMB1 fill:#4ecdc4,color:#fff
    style DMB2 fill:#4ecdc4,color:#fff
    style VBT fill:#9b59b6,color:#fff
    style CALC fill:#f39c12,color:#fff
```

## Multi-Chain Configuration

```mermaid
flowchart TB
    subgraph ESP["One ESP32 publishing 8 batteries"]
        MQTT["battery/bms1...bms8"]
    end

    subgraph Services["Two Dbus Services"]
        S1["dbus-mqtt-battery\n(bms-first: 1, batteries: 4)"]
        S2["dbus-mqtt-battery\n(bms-first: 5, batteries: 4)"]
    end

    MQTT --> S1; MQTT --> S2
    S1 --> C1["mqtt_chain1"]; S2 --> C2["mqtt_chain2"]

    style S1 fill:#4ecdc4,color:#fff
    style S2 fill:#4ecdc4,color:#fff
```

## Runbook: Troubleshooting

### Package Not Showing in PackageManager

**Actions:**
```bash
# Verify version file
cat /data/dbus-mqtt-battery/version

# Verify setup script
ls -la /data/dbus-mqtt-battery/setup

# Restart PackageManager
svc -t /service/PackageManager
```

### Chain Shows N/A

**Actions:**
```bash
# Check ESP32 is publishing
mosquitto_sub -v -t 'battery/#' -C 5

# Check service is running
svstat /service/dbus-mqtt-chain1

# Verify D-Bus service
dbus -y com.victronenergy.battery.mqtt_chain1 /Dc/0/Voltage GetValue
```

---

## Related Documentation

- [inverter-control System Architecture](../inverter-control/.github/docs/system-architecture.md)
- [ADR-003: DVCC for JBD BMS Protection](../inverter-control/.github/docs/adr-001-grid-zero-architecture.md)
