"""
MQTT client for receiving battery data from ESP32.

Handles MQTT connection, auto-reconnect with exponential backoff,
and data aggregation from multiple batteries.
"""

from __future__ import annotations

import logging
import math
import os
import re
from threading import RLock
from time import monotonic, time
from typing import Any

from .bms_data import STALE_TIMEOUT, BatteryData
from .telemetry import decode_batteries, decode_header, merge_partial

logger = logging.getLogger("MqttBattery")
MAX_RETIRED_BOOT_IDS = 4096

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
        temps_per_bms: int = 2,
        telemetry_mode: str = "atomic",
    ) -> None:
        self.broker = broker
        self.port = port
        self.battery_count = battery_count
        self.topic_prefix = topic_prefix
        self.installed_capacity = installed_capacity
        # MQTT topic index of first BMS for this chain (chain1: 1, chain2 with 2 BMS: 3 for bms3,bms4)
        self.bms_first = max(1, bms_first)
        self.cells_per_bms = cells_per_bms
        # Stable temperature ID stride (mirrors cells_per_bms). Must be configured
        # >= densest BMS sensor count; never derived from the current valid set.
        self.temps_per_bms = max(1, temps_per_bms)

        # Create battery data containers (1-indexed for bms1, bms2, etc.)
        self.batteries: dict[int, BatteryData] = {
            i: BatteryData(i, cells_per_bms) for i in range(1, battery_count + 1)
        }
        self._data_lock = RLock()
        if telemetry_mode not in ("atomic", "legacy"):
            raise ValueError("telemetry_mode must be atomic or legacy")
        self.telemetry_source = telemetry_mode
        self.telemetry_error = "Waiting for first complete live BMS readings"
        self._boot_id: str | None = None
        self._sequence = -1
        self._has_full_snapshot = False
        self._boot_ready = False
        self.telemetry_partial = False
        self._retired_boot_ids: set[str] = set()

        # Aggregate totals from ESP32
        self.total_voltage: float = 0.0
        self.total_current: float = 0.0
        self.total_power: float = 0.0
        self.total_soc: float = 0.0
        self.total_capacity: float = 0.0
        self._total_updated: dict[str, float] = {}
        # Track if ESP publishes current_total (some ESPHome configs don't)
        self.current_total_seen: bool = False
        self.soc_total_seen: bool = False
        # Timestamp of last MQTT message received (any topic) for staleness alarms
        self.last_message_time: float = 0.0

        # MQTT client (handle both paho-mqtt v1 and v2)
        # topic_prefix+pid: two chains started in the same second got identical ids
        # and fought over the session (SessionTakenOver every second).
        client_id = f"dbus-mqtt-battery-{topic_prefix}-{os.getpid()}"
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
        with self._data_lock:
            self._boot_ready = False
            if self.telemetry_source == "atomic" and self._has_full_snapshot:
                self.telemetry_partial = True
                self.telemetry_error = "MQTT disconnected; using unexpired physical evidence"
            else:
                self._invalidate_samples("MQTT disconnected; waiting for new live readings")
        if rc != 0:
            logger.warning("MQTT disconnected unexpectedly (rc=%s), will auto-reconnect", rc)
        else:
            logger.info("MQTT disconnected cleanly")

    def _on_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """MQTT message callback."""
        # Retained values predate this subscription; the legacy protocol has no
        # trustworthy source timestamp. They cannot initialize or renew a BMS.
        if getattr(msg, "retain", False):
            return
        with self._data_lock:
            self._process_message(msg)

    def _invalidate_samples(self, reason: str) -> None:
        self._has_full_snapshot = False
        self._boot_ready = False
        self.telemetry_partial = False
        self.batteries = {
            i: BatteryData(i, self.cells_per_bms) for i in range(1, self.battery_count + 1)
        }
        self._total_updated.clear()
        self.telemetry_error = reason

    def _process_message(self, msg: Any) -> None:
        self.last_message_time = time()
        try:
            topic = msg.topic
            payload = msg.payload.decode("utf-8").strip()
            if topic == f"{self.topic_prefix}/telemetry":
                # Atomic producers must never fall back to cached legacy topics.
                self.telemetry_source = "atomic"
                self._process_telemetry(payload)
                return
            if self.telemetry_source == "atomic":
                if topic == f"{self.topic_prefix}/status" and payload.lower() == "offline":
                    # Availability is transport evidence, never a BMS permission.
                    # Preserve the original physical deadlines during a reboot.
                    self._boot_ready = False
                    if self._has_full_snapshot:
                        self.telemetry_partial = True
                        self.telemetry_error = "Producer offline; using unexpired physical evidence"
                return
            if not topic.startswith(f"{self.topic_prefix}/"):
                return

            # Parse topic: battery/sensor/voltage_bms1/state
            #           or battery/binary_sensor/charging_bms1/state
            parts = topic[len(self.topic_prefix) + 1 :].split("/")
            if len(parts) < 3:
                return

            # "sensor" or "binary_sensor" - extracted but not currently used
            _sensor_type = parts[0]
            sensor_name = parts[1]  # "voltage_bms1", "voltage_total", etc.

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

        except Exception as e:  # noqa: BLE001 - keep MQTT loop alive on any bad payload
            if msg.topic == f"{self.topic_prefix}/telemetry":
                self.telemetry_source = "atomic"
                self._invalidate_samples(f"Unreadable atomic telemetry: {e}")
            logger.debug("Error processing MQTT message: %s", e)

    def _process_telemetry(self, payload: str) -> None:
        """Accept one ordered, complete producer frame under the aggregate lock."""
        try:
            frame, boot_id, sequence = decode_header(payload)
            if boot_id in self._retired_boot_ids or (
                boot_id == self._boot_id and sequence <= self._sequence
            ):
                return
            if self._boot_id is not None and self._boot_id != boot_id:
                if len(self._retired_boot_ids) >= MAX_RETIRED_BOOT_IDS:
                    # Never evict replay protection to admit a new producer.
                    # Retain only the already accepted evidence and its expiry;
                    # the current session can still send newer frames.
                    reason = "Producer-session history full; restart driver to admit a new boot"
                    if self.telemetry_error != reason:
                        logger.warning("%s", reason)
                    self.telemetry_error = reason
                    self.telemetry_partial = True
                    return
                self._retired_boot_ids.add(self._boot_id)
                self._boot_ready = False
            self._boot_id, self._sequence = boot_id, sequence
            if frame["ready"]:
                batteries = decode_batteries(
                    frame, self.battery_count, self.bms_first, self.cells_per_bms, monotonic()
                )
                self._has_full_snapshot = True
                self._boot_ready = True
                self.telemetry_partial = False
            elif self._has_full_snapshot:
                batteries = merge_partial(
                    frame, self.batteries, self.bms_first, monotonic(), refresh=self._boot_ready
                )
                self.telemetry_partial = True
            else:
                self._invalidate_samples("Waiting for first complete physical BMS frame")
                return
            self.batteries = batteries
            self._total_updated.clear()
            self.telemetry_error = (
                "" if frame["ready"] else "Incomplete poll; missing fields keep original expiry"
            )
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            self._invalidate_samples(f"Rejected atomic telemetry: {error}")
            logger.warning("%s", self.telemetry_error)

    def missing_data_reason(self) -> str:
        """Describe absent or expired physical readings under the snapshot lock."""
        with self._data_lock:
            missing = [
                f"BMS {index + self.bms_first - 1}: {', '.join(battery.missing_fields())}"
                for index, battery in self.batteries.items()
                if not battery.is_valid()
            ]
            return "; ".join(missing) or self.telemetry_error

    def _collect_cells_and_temps(
        self, valid_batts: list[dict[str, Any]]
    ) -> tuple[list[tuple[int, float]], list[tuple[int, float]], list[float], bool]:
        """Collect cells and temperatures with global IDs from all battery snapshots.

        Returns (cells_with_id, temps_with_id, all_temp_values, temp_ids_unambiguous).

        Temperature global IDs use the configured ``temps_per_bms`` stride and are
        never renumbered when a BMS drops out. When any sensor index exceeds that
        stride (undersized config), IDs are omitted from the export so callers do
        not publish ambiguous Min/MaxTemperatureCellId values, while raw
        temperature values remain available for safety extrema.
        """
        all_cells_with_id = []
        cells_per_bms = self.cells_per_bms
        temps_per_bms = self.temps_per_bms

        pending_temps: list[tuple[int, int, float]] = []  # battery_id, temp_idx, temp
        all_temp_values: list[float] = []
        oversized = False

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
                    all_temp_values.append(temp)
                    if temp_idx > temps_per_bms:
                        oversized = True
                        logger.error(
                            "temperature index %s exceeds temps_per_bms=%s for battery %s; "
                            "set temps_per_bms >= densest BMS sensor count "
                            "(IDs withheld to avoid collisions; extrema values still computed)",
                            temp_idx,
                            temps_per_bms,
                            batt["battery_id"],
                        )
                    else:
                        pending_temps.append((batt["battery_id"], temp_idx, temp))

        all_temps_with_id: list[tuple[int, float]] = []
        if oversized:
            # Undersized stride: do not emit any temperature IDs (would collide /
            # mix packs). Values remain in all_temp_values for extrema.
            return all_cells_with_id, all_temps_with_id, all_temp_values, False

        seen_ids: set[int] = set()
        for battery_id, temp_idx, temp in pending_temps:
            global_id = (battery_id - 1) * temps_per_bms + temp_idx
            if global_id in seen_ids:
                logger.error(
                    "duplicate temperature global_id %s under temps_per_bms=%s; "
                    "withholding temperature IDs (raise temps_per_bms)",
                    global_id,
                    temps_per_bms,
                )
                return all_cells_with_id, [], all_temp_values, False
            seen_ids.add(global_id)
            all_temps_with_id.append((global_id, temp))

        return all_cells_with_id, all_temps_with_id, all_temp_values, True

    @staticmethod
    def _cell_extremes(
        all_cells_with_id: list[tuple[int, float]],
    ) -> tuple[float | None, int | None, float | None, int | None]:
        """Return (min_voltage, min_id, max_voltage, max_id), or all None when empty."""
        if not all_cells_with_id:
            return None, None, None, None
        low = min(all_cells_with_id, key=lambda c: c[1])
        high = max(all_cells_with_id, key=lambda c: c[1])
        return low[1], low[0], high[1], high[0]

    @staticmethod
    def _temp_extremes(
        all_temps_with_id: list[tuple[int, float]],
    ) -> tuple[float, int, float, int]:
        """Return (min_temp, min_id, max_temp, max_id); caller guarantees non-empty list."""
        low = min(all_temps_with_id, key=lambda t: t[1])
        high = max(all_temps_with_id, key=lambda t: t[1])
        return low[1], low[0], high[1], high[0]

    def _compute_electrical_totals(
        self,
        valid_batts: list[dict[str, Any]],
        total_capacity_remaining: float,
        totals: dict[str, float] | None = None,
    ) -> tuple[float, float, float, float, float]:
        """Compute aggregate voltage/current/power/soc/capacity.

        Uses ESP32 totals when fresh; otherwise derives values from per-BMS data.
        Important: many ESPHome configs publish voltage_total but NOT current_total.
        In that case total_current stays 0 and D-Bus showed 0A — use per-BMS current instead.
        """
        if totals is None:
            totals = self._fresh_totals()

        voltage = totals.get("total_voltage", 0.0)
        if voltage <= 0:
            voltage = sum(b["voltage"] for b in valid_batts)
        current = totals.get(
            "total_current", sum(b["current"] for b in valid_batts) / len(valid_batts)
        )
        if "total_power" in totals:
            power = totals["total_power"]
        elif "total_current" in totals:
            power = voltage * current
        else:
            power = sum(b["power"] for b in valid_batts)
            if abs(power) < 1.0:
                power = voltage * current
        soc = totals.get("total_soc", min(b["soc"] for b in valid_batts))
        if soc < 0:
            soc = min(b["soc"] for b in valid_batts)
        capacity = totals.get("total_capacity", total_capacity_remaining)
        if capacity < 0:
            capacity = total_capacity_remaining
        return voltage, current, power, soc, capacity

    def _fresh_totals(self) -> dict[str, float]:
        with self._data_lock:
            now = monotonic()
            return {
                name: getattr(self, name)
                for name, updated in self._total_updated.items()
                if 0 <= now - updated < STALE_TIMEOUT
            }

    def _update_total(self, sensor_name: str, value: str) -> None:
        """Refresh only the recognized aggregate field received in this message."""
        fields = {
            "voltage_total": "total_voltage",
            "current_total": "total_current",
            "power_total": "total_power",
            "soc_total": "total_soc",
            "capacity_total": "total_capacity",
        }
        attribute = fields.get(sensor_name)
        if attribute is None:
            return
        try:
            val = float(value)
        except (TypeError, ValueError):
            return
        if not math.isfinite(val):
            return
        with self._data_lock:
            setattr(self, attribute, val)
            self._total_updated[attribute] = monotonic()
            if attribute == "total_current":
                self.current_total_seen = True
            elif attribute == "total_soc":
                self.soc_total_seen = True

    def get_aggregate_data(self) -> dict[str, Any] | None:
        """Get aggregated data from all batteries (thread-safe)."""
        # Copy battery data under lock to avoid race conditions with MQTT thread
        with self._data_lock:
            batt_snapshots: list[dict[str, Any]] = []
            oldest_samples: list[float] = []
            for b in self.batteries.values():
                with b.lock:
                    if not b.is_valid():
                        continue
                    oldest_sample = b.oldest_sample_time
                    # Enforce the snapshot contract even under python -O.
                    if b.voltage is None or b.current is None or oldest_sample is None:
                        continue
                    batt_snapshots.append(
                        {
                            "battery_id": b.battery_id,
                            "voltage": b.voltage,
                            "current": b.current,
                            "power": b.power if b.power is not None else b.voltage * b.current,
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
                    oldest_samples.append(oldest_sample)
            if not batt_snapshots:
                return None
            # Copy totals and physical timestamps under the same lock as cells
            # and permissions. A new producer frame cannot tear this snapshot.
            totals = self._fresh_totals()
            telemetry_source = self.telemetry_source
            telemetry_partial = self.telemetry_partial
            missing_data = self.missing_data_reason()

        # Process snapshots outside of locks
        valid_batts = batt_snapshots
        missing_count = self.battery_count - len(valid_batts)
        data_complete = missing_count == 0

        # Collect all cells with global IDs: (global_cell_id, voltage)
        # Global ID = (bms_id - 1) * cells_per_bms + cell_idx
        (
            all_cells_with_id,
            all_temps_with_id,
            all_temp_values,
            temp_ids_unambiguous,
        ) = self._collect_cells_and_temps(valid_batts)

        # Find min/max cells
        min_cell_voltage, min_cell_id, max_cell_voltage, max_cell_id = self._cell_extremes(
            all_cells_with_id
        )

        # Find min/max temperatures (values always from every sensor; IDs only
        # when the configured stride cannot collide).
        min_temp_id: int | None = None
        max_temp_id: int | None = None
        if all_temps_with_id and temp_ids_unambiguous:
            min_temp, min_temp_id, max_temp, max_temp_id = self._temp_extremes(all_temps_with_id)
        elif all_temp_values:
            min_temp = min(all_temp_values)
            max_temp = max(all_temp_values)
            min_temp_id = None
            max_temp_id = None
        else:
            min_temp = sum(b["temperature"] for b in valid_batts) / len(valid_batts)
            max_temp = min_temp

        # Calculate capacity for SERIES-connected batteries (4S configuration)
        # In series: voltage adds up, capacity stays the same
        total_capacity_full = self.installed_capacity

        # Remaining capacity = installed × average SoC / 100
        avg_soc = sum(b["soc"] for b in valid_batts) / len(valid_batts)
        total_capacity_remaining = total_capacity_full * avg_soc / 100

        # Use ESP32 totals if available, otherwise calculate from per-BMS data
        voltage, current, power, soc, capacity = self._compute_electrical_totals(
            valid_batts, total_capacity_remaining, totals
        )

        return {
            "data_complete": data_complete,
            "oldest_sample_time": min(oldest_samples),
            "telemetry_source": telemetry_source,
            "telemetry_partial": telemetry_partial,
            "missing_data": missing_data if not data_complete or telemetry_partial else "",
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
            "allow_charge": data_complete and all(b["charging"] for b in valid_batts),
            "allow_discharge": data_complete and all(b["discharging"] for b in valid_batts),
            "cycles": max(
                (b["cycles"] for b in valid_batts if b["cycles"] is not None), default=None
            ),
            "modules_online": sum(1 for b in valid_batts if b["online"]),
            "modules_offline": missing_count,
            "modules_blocking_discharge": missing_count
            + sum(1 for b in valid_batts if not b["discharging"]),
            "modules_blocking_charge": missing_count
            + sum(1 for b in valid_batts if not b["charging"]),
            "all_cells": all_cells_with_id,  # List of (global_id, voltage) tuples
            "temperatures": {
                b["battery_id"]: b["temperature"] for b in valid_batts
            },  # BMS ID -> temp
        }
