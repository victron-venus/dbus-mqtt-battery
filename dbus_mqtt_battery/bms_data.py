"""
Battery data container for single BMS unit.

Thread-safe container for battery parameters received from MQTT.
"""

from __future__ import annotations

import math
from threading import RLock
from time import monotonic
from typing import Any

# Stale data timeout (seconds)
STALE_TIMEOUT = 60

# Battery parameter keys grouped by conversion type
_FLOAT_KEYS = frozenset(
    {"voltage", "current", "power", "soc", "capacity_remaining", "capacity_total"}
)
_INT_KEYS = frozenset({"cycles"})
_BOOL_KEYS = frozenset({"charging", "discharging", "balancing", "online"})
_LIVE_KEYS = frozenset({"voltage", "current", "power", "soc"})


class BatteryData:
    """Container for single battery data from MQTT."""

    __slots__ = (
        "_sample_times",
        "balancing",
        "battery_id",
        "capacity_remaining",
        "capacity_total",
        "cell_count",
        "cells",
        "charging",
        "current",
        "cycles",
        "discharging",
        "last_update",
        "lock",
        "online",
        "power",
        "soc",
        "temperature",
        "temperatures",
        "voltage",
    )

    def __init__(self, battery_id: int) -> None:
        self.battery_id = battery_id
        self.voltage: float = 0.0
        self.current: float = 0.0
        self.power: float = 0.0
        self.soc: float = 0.0
        self.capacity_remaining: float = 0.0
        self.capacity_total: float = 0.0
        self.cycles: int = 0
        self.temperature: float = 25.0
        self.temperatures: dict[int, float] = {}  # sensor_index -> temperature
        self.cells: dict[int, float] = {}  # cell_index -> voltage
        self.cell_count: int = 4
        self.charging: bool = True
        self.discharging: bool = True
        self.balancing: bool = False
        self.online: bool = True
        self.last_update: float = 0.0
        self.lock = RLock()
        self._sample_times: dict[str, float] = {}

    def update(self, key: str, value: Any) -> None:
        """Update a battery parameter."""
        with self.lock:
            if key in _BOOL_KEYS:
                text = str(value).upper()
                if text not in ("ON", "TRUE", "1", "OFF", "FALSE", "0"):
                    return
                setattr(self, key, text in ("ON", "TRUE", "1"))
                return
            try:
                numeric = float(value)
                if not math.isfinite(numeric):
                    return
                if key == "temperature":
                    key = "temperature_1"
                if key.startswith("temperature_"):
                    self._update_temperature_sensor(key, numeric)
                elif key.startswith("cell_"):
                    self._update_cell_voltage(key, numeric)
                elif key in _FLOAT_KEYS:
                    setattr(self, key, numeric)
                elif key in _INT_KEYS:
                    setattr(self, key, int(numeric))
                else:
                    return
            except (TypeError, ValueError, OverflowError):
                return
            # Status, capacity and cycle topics cannot renew live measurements.
            if key in _LIVE_KEYS or key.startswith(("cell_", "temperature_")):
                self.last_update = monotonic()
                self._sample_times[key] = self.last_update

    def _update_temperature_sensor(self, key: str, value: Any) -> None:
        """Store a single sensor reading and refresh the average temperature."""
        temp_idx = self._sensor_index(key)
        self.temperatures[temp_idx] = float(value)
        valid_temps = [t for t in self.temperatures.values() if t > -40]
        if valid_temps:
            self.temperature = sum(valid_temps) / len(valid_temps)

    def _update_cell_voltage(self, key: str, value: Any) -> None:
        """Store a single cell voltage and track the highest cell count seen."""
        cell_idx = self._sensor_index(key)
        self.cells[cell_idx] = float(value)
        self.cell_count = max(self.cell_count, len(self.cells))

    @staticmethod
    def _sensor_index(key: str) -> int:
        """Reject malformed sensor names before recording their freshness."""
        index = int(key.split("_", 1)[1])
        if index < 1:
            raise ValueError("Sensor index must be positive")
        return index

    def get_min_temperature(self) -> tuple[float, int]:
        """Returns (min_temp, sensor_id)."""
        valid = [(idx, t) for idx, t in self.temperatures.items() if t > -40]
        if not valid:
            return self.temperature, 1
        min_temp = min(valid, key=lambda x: x[1])
        return min_temp[1], min_temp[0]

    def get_max_temperature(self) -> tuple[float, int]:
        """Returns (max_temp, sensor_id)."""
        valid = [(idx, t) for idx, t in self.temperatures.items() if t > -40]
        if not valid:
            return self.temperature, 1
        max_temp = max(valid, key=lambda x: x[1])
        return max_temp[1], max_temp[0]

    def is_valid(self) -> bool:
        """Require fresh voltage and every live measurement this BMS publishes.

        Optional, never-published sensors do not become mandatory. Binary BMS
        status is change-driven and remains latched while measurements are fresh.
        """
        with self.lock:
            now = monotonic()
            return (
                self.online
                and self.voltage > 0
                and "voltage" in self._sample_times
                and all(
                    0 <= now - updated < STALE_TIMEOUT for updated in self._sample_times.values()
                )
            )

    def get_min_cell_voltage(self) -> tuple[float | None, int | None]:
        """Returns (min_voltage, cell_id)."""
        valid = [(idx, v) for idx, v in self.cells.items() if v and v > 0]
        if not valid:
            return None, None
        min_cell = min(valid, key=lambda x: x[1])
        return min_cell[1], min_cell[0]

    def get_max_cell_voltage(self) -> tuple[float | None, int | None]:
        """Returns (max_voltage, cell_id)."""
        valid = [(idx, v) for idx, v in self.cells.items() if v and v > 0]
        if not valid:
            return None, None
        max_cell = max(valid, key=lambda x: x[1])
        return max_cell[1], max_cell[0]
