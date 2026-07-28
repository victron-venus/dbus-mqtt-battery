"""
MQTT client for receiving battery data from ESP32.

Handles MQTT connection, auto-reconnect with exponential backoff,
and data aggregation from multiple batteries.
"""

from __future__ import annotations

import logging
import re
from threading import Lock
from time import time
from typing import Any

from .bms_data import BatteryData, STALE_TIMEOUT

logger = logging.getLogger("MqttBattery")

# Supported paho-mqtt versions
try:
    import paho.mqtt.client as mqtt

    try:
        from paho.mqtt.enums import CallbackAPIVersion

        PAHO_V2 = True
    except ImportError:
        PAHO_V2 = False
except ImportError as exc:
    raise RuntimeError("paho-mqtt not installed. Run: pip install paho-mqtt") from exc


class MqttBatteryClient:
    """MQTT client that receives battery data from ESP32."""

    def __init__(
        self,
        broker: str,
        port: int,
        battery_count: int = 4,
        topic_prefix: str = "battery",
        installed_capacity: float = 280,
        bms_first: int = 1,
        cells_per_bms: int = 4,
    ) -> None:
        self.broker = broker
        self.port = port
        self.battery_count = battery_count
        self.topic_prefix = topic_prefix
        self.installed_capacity = installed_capacity
        # MQTT topic index of first BMS for this chain (chain1: 1, chain2 with 2 BMS: 3 for bms3,bms4)
        self.bms_first = max(1, bms_first)
        self.cells_per_bms = cells_per_bms

        # Create battery data containers (1-indexed for bms1, bms2, etc.)
        self.batteries: dict[int, BatteryData] = {
            i: BatteryData(i) for i in range(1, battery_count + 1)
        }
        self._data_lock = Lock()

        # Aggregate totals from ESP32
        self.total_voltage: float = 0.0
        self.total_current: float = 0.0
        self.total_power: float = 0.0
        self.total_soc: float = 0.0
        self.total_capacity: float = 0.0
        self.total_updated: float = 0.0
        # Track if ESP publishes current_total (some ESPHome configs don't)
        self.current_total_seen: bool = False
        self.soc_total_seen: bool = False

        # MQTT client (handle both paho-mqtt v1 and v2)
        client_id = f"dbus-mqtt-battery-{int(time())}"
        if PAHO_V2:
            self.client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION1, client_id=client_id
            )
        else:
            self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.connected = False
        self._max_reconnect_delay = 60

    def connect(self) -> bool:
        """Connect to MQTT broker with auto-reconnect enabled."""
        try:
            logger.info("Connecting to MQTT broker %s:%s", self.broker, self.port)
            self.client.reconnect_delay_set(min_delay=1, max_delay=self._max_reconnect_delay)
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            return True
        except Exception:
            logger.exception("MQTT connection failed")
            return False

    def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client: Any, userdata: Any, flags: Any, rc: int) -> None:
        """MQTT connection callback."""
        if rc == 0:
            logger.info("Connected to MQTT broker")
            self.connected = True
            topic = f"{self.topic_prefix}/#"
            client.subscribe(topic)
            logger.info("Subscribed to %s", topic)
        else:
            logger.exception("MQTT connection failed with code %s", rc)

    def _on_disconnect(self, client: Any, userdata: Any, rc: int) -> None:
        """MQTT disconnection callback with auto-reconnect."""
        self.connected = False
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly (rc=%s), will auto-reconnect", rc)
        else:
            logger.info("MQTT disconnected cleanly")

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """MQTT message callback."""
        try:
            topic = msg.topic
            payload = msg.payload.decode("utf-8").strip()

            # Parse topic: battery/sensor/voltage_bms1/state
            #           or battery/binary_sensor/charging_bms1/state
            parts = topic.split("/")
            if len(parts) < 3:
                return

            # "sensor" or "binary_sensor" - extracted but not currently used
            _sensor_type = parts[1]
            sensor_name = parts[2]  # "voltage_bms1", "voltage_total", etc."

            # Handle totals
            if sensor_name.endswith("_total"):
                self._update_total(sensor_name, payload)
                return

            # Extract battery index from name (e.g., "voltage_bms1" -> bms1 -> 1)
            match = re.search(r"bms(\d+)$", sensor_name)
            if not match:
                return

            bms_idx_mqtt = int(match.group(1))
            # Map MQTT bms index to internal slot (chain2: bms3,bms4 -> internal 1,2)
            bms_idx = bms_idx_mqtt - self.bms_first + 1
            if bms_idx < 1 or bms_idx > self.battery_count:
                return

            # Extract sensor type (e.g., "voltage_bms1" -> "voltage")
            sensor_key = re.sub(r"_bms\d+$", "", sensor_name)

            # Map sensor names to battery attributes
            mapping = {
                "voltage": "voltage",
                "current": "current",
                "power": "power",
                "soc": "soc",
                "capacity_remaining": "capacity_remaining",
                "capacity": "capacity_total",
                "cycles": "cycles",
                "charging": "charging",
                "discharging": "discharging",
                "balancing": "balancing",
                "online": "online",
            }

            # Handle cell voltages: voltage_cell1 -> cell_1
            if sensor_key.startswith("voltage_cell"):
                cell_num = sensor_key.replace("voltage_cell", "")
                self.batteries[bms_idx].update(f"cell_{cell_num}", payload)
            # Handle temperature sensors: temperature1 -> temperature_1
            elif sensor_key.startswith("temperature"):
                temp_num = sensor_key.replace("temperature", "")
                if temp_num:
                    self.batteries[bms_idx].update(f"temperature_{temp_num}", payload)
                else:
                    self.batteries[bms_idx].update("temperature", payload)
            elif sensor_key in mapping:
                self.batteries[bms_idx].update(mapping[sensor_key], payload)

        except Exception as e:
            logger.debug("Error processing MQTT message: %s", e)

    def _update_total(self, sensor_name: str, value: str) -> None:
        """Update aggregate totals."""
        try:
            val = float(value)
            if sensor_name == "voltage_total":
                self.total_voltage = val
            elif sensor_name == "current_total":
                self.total_current = val
                self.current_total_seen = True
            elif sensor_name == "power_total":
                self.total_power = val
            elif sensor_name == "soc_total":
                self.total_soc = val
                self.soc_total_seen = True
            elif sensor_name == "capacity_total":
                self.total_capacity = val
            self.total_updated = time()
        except (TypeError, ValueError):
            pass

    def get_aggregate_data(self) -> dict[str, Any] | None:
        """Get aggregated data from all batteries (thread-safe)."""
        # Copy battery data under lock to avoid race conditions with MQTT thread
        with self._data_lock:
            valid_batts = [b for b in self.batteries.values() if b.is_valid()]
            if not valid_batts:
                return None
            # Copy volatile data from each battery
            batt_snapshots = []
            for b in valid_batts:
                with b.lock:
                    batt_snapshots.append(
                        {
                            "battery_id": b.battery_id,
                            "voltage": b.voltage,
                            "current": b.current,
                            "power": b.power,
                            "soc": b.soc,
                            "capacity_remaining": b.capacity_remaining,
                            "temperature": b.temperature,
                            "temperatures": dict(b.temperatures),
                            "cells": dict(b.cells),
                            "cell_count": b.cell_count,
                            "charging": b.charging,
                            "discharging": b.discharging,
                            "cycles": b.cycles,
                            "online": b.online,
                        }
                    )

        # Process snapshots outside of locks
        valid_batts = batt_snapshots

        # Collect all cells with global IDs: (global_cell_id, voltage)
        # Global ID = (bms_id - 1) * cells_per_bms + cell_idx
        all_cells_with_id = []
        all_temps_with_id = []
        cells_per_bms = self.cells_per_bms

        for batt in valid_batts:
            for cell_idx, voltage in batt["cells"].items():
                if voltage and voltage > 0:
                    # Offset global IDs when this chain starts at bms N > 1
                    chain_cell_base = (self.bms_first - 1) * cells_per_bms
                    global_id = (
                        chain_cell_base + (batt["battery_id"] - 1) * cells_per_bms + cell_idx
                    )
                    all_cells_with_id.append((global_id, voltage))
            for temp_idx, temp in batt["temperatures"].items():
                if temp > -40:
                    global_id = (batt["battery_id"] - 1) * 2 + temp_idx
                    all_temps_with_id.append((global_id, temp))

        # Find min/max cells
        min_cell_voltage: float | None = None
        min_cell_id: int | None = None
        max_cell_voltage: float | None = None
        max_cell_id: int | None = None
        if all_cells_with_id:
            min_cell = min(all_cells_with_id, key=lambda x: x[1])
            max_cell = max(all_cells_with_id, key=lambda x: x[1])
            min_cell_voltage, min_cell_id = min_cell[1], min_cell[0]
            max_cell_voltage, max_cell_id = max_cell[1], max_cell[0]

        # Find min/max temperatures
        min_temp_id: int = 1
        max_temp_id: int = 1
        min_temp: float
        max_temp: float
        if all_temps_with_id:
            min_t = min(all_temps_with_id, key=lambda x: x[1])
            max_t = max(all_temps_with_id, key=lambda x: x[1])
            min_temp, min_temp_id = min_t[1], min_t[0]
            max_temp, max_temp_id = max_t[1], max_t[0]
        else:
            min_temp = sum(b["temperature"] for b in valid_batts) / len(valid_batts)
            max_temp = min_temp

        # Calculate capacity for SERIES-connected batteries (4S configuration)
        # In series: voltage adds up, capacity stays the same
        total_capacity_full = self.installed_capacity

        # Remaining capacity = installed × average SoC / 100
        avg_soc = sum(b["soc"] for b in valid_batts) / len(valid_batts)
        total_capacity_remaining = total_capacity_full * avg_soc / 100

        # Use ESP32 totals if available, otherwise calculate
        # Important: many ESPHome configs publish voltage_total but NOT current_total.
        # In that case total_current stays 0 and D-Bus showed 0A — use per-BMS current instead.
        if (time() - self.total_updated) < STALE_TIMEOUT and self.total_voltage > 0:
            voltage = self.total_voltage
            if self.current_total_seen:
                current = self.total_current
                power = (
                    self.total_power
                    if self.total_power != 0
                    else self.total_voltage * self.total_current
                )
            else:
                current = sum(b["current"] for b in valid_batts) / len(valid_batts)
                power = sum(b["power"] for b in valid_batts)
                if abs(power) < 1.0:
                    power = voltage * current
            if self.soc_total_seen and self.total_soc > 0:
                soc = self.total_soc
                capacity = (
                    self.total_capacity if self.total_capacity > 0 else total_capacity_remaining
                )
            else:
                soc = min(b["soc"] for b in valid_batts)
                capacity = total_capacity_remaining
        else:
            voltage = sum(b["voltage"] for b in valid_batts)
            current = sum(b["current"] for b in valid_batts) / len(valid_batts)
            power = sum(b["power"] for b in valid_batts)
            soc = min(b["soc"] for b in valid_batts)
            capacity = total_capacity_remaining

        return {
            "voltage": voltage,
            "current": current,
            "power": power,
            "soc": soc,
            "capacity": capacity,
            "capacity_full": total_capacity_full,
            "min_cell": min_cell_voltage,
            "min_cell_id": min_cell_id,
            "max_cell": max_cell_voltage,
            "max_cell_id": max_cell_id,
            "min_temp": min_temp,
            "min_temp_id": min_temp_id,
            "max_temp": max_temp,
            "max_temp_id": max_temp_id,
            "temperature": sum(b["temperature"] for b in valid_batts) / len(valid_batts),
            "cell_count": sum(b["cell_count"] for b in valid_batts),
            "allow_charge": all(b["charging"] for b in valid_batts),
            "allow_discharge": all(b["discharging"] for b in valid_batts),
            "cycles": max(b["cycles"] for b in valid_batts),
            "modules_online": sum(1 for b in valid_batts if b["online"]),
            "modules_offline": sum(1 for b in valid_batts if not b["online"]),
            "modules_blocking_discharge": sum(1 for b in valid_batts if not b["discharging"]),
            "modules_blocking_charge": sum(1 for b in valid_batts if not b["charging"]),
            "all_cells": all_cells_with_id,  # List of (global_id, voltage) tuples
            "temperatures": {
                b["battery_id"]: b["temperature"] for b in valid_batts
            },  # BMS ID -> temp
        }
