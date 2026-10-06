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

    def __init__(self, battery_id: int, cells_per_bms: int = 4) -> None:
        self.battery_id = battery_id
        self.voltage: float | None = None
        self.current: float | None = None
        self.power: float | None = None
        self.soc: float | None = None
        self.capacity_remaining: float | None = None
        self.capacity_total: float | None = None
        self.cycles: int | None = None
        self.temperature: float | None = None
        self.temperatures: dict[int, float] = {}  # sensor_index -> temperature
        self.cells: dict[int, float] = {}  # cell_index -> voltage
        self.cell_count: int = cells_per_bms
        self.charging: bool | None = None
        self.discharging: bool | None = None
        self.balancing: bool = False
        self.online: bool | None = None
        self.last_update: float = 0.0
        self.lock = RLock()
        self._sample_times: dict[str, float] = {}

    def update(self, key: str, value: Any) -> None:
        """Update a battery parameter."""
        with self.lock:
            if key == "temperature":
                key = "temperature_1"
            if key in _BOOL_KEYS:
                text = str(value).upper()
                if text not in ("ON", "TRUE", "1", "OFF", "FALSE", "0"):
                    if key in ("charging", "discharging", "online"):
                        setattr(self, key, None)
                    return
                setattr(self, key, text in ("ON", "TRUE", "1"))
                return
            try:
                numeric = float(value)
                if not math.isfinite(numeric):
                    if key in self._sample_times:
                        self._sample_times[key] = float("-inf")
                    return
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
                if key in self._sample_times:
                    self._sample_times[key] = float("-inf")
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
        self.temperature = sum(valid_temps) / len(valid_temps) if valid_temps else None

    def _update_cell_voltage(self, key: str, value: Any) -> None:
        """Store a single cell voltage and track the highest cell count seen."""
        cell_idx = self._sensor_index(key)
        self.cells[cell_idx] = float(value)

    @staticmethod
    def _sensor_index(key: str) -> int:
        """Reject malformed sensor names before recording their freshness."""
        index = int(key.split("_", 1)[1])
        if index < 1:
            raise ValueError("Sensor index must be positive")
        return index

    def get_min_temperature(self) -> tuple[float | None, int]:
        """Returns (min_temp, sensor_id)."""
        valid = [(idx, t) for idx, t in self.temperatures.items() if t > -40]
        if not valid:
            return self.temperature, 1
        min_temp = min(valid, key=lambda x: x[1])
        return min_temp[1], min_temp[0]

    def get_max_temperature(self) -> tuple[float | None, int]:
        """Returns (max_temp, sensor_id)."""
        valid = [(idx, t) for idx, t in self.temperatures.items() if t > -40]
        if not valid:
            return self.temperature, 1
        max_temp = max(valid, key=lambda x: x[1])
        return max_temp[1], max_temp[0]

    def missing_fields(self) -> list[str]:
        """Describe incomplete or expired required physical measurements.

        Legacy binary topics are change-driven: an explicitly received permission
        remains latched while *all* physical readings remain fresh. Never-seen
        permissions are unknown, not implicit permission. Atomic telemetry replaces
        the entire object each frame, including permissions and their source age.
        """
        with self.lock:
            now = monotonic()
            required = {"voltage", "current", "soc"}
            required.update(f"cell_{index}" for index in range(1, self.cell_count + 1))
            required.update(f"temperature_{index}" for index in self.temperatures)
            if not self.temperatures:
                required.add("temperature_1")
            missing = sorted(
                key
                for key in required | self._sample_times.keys()
                if key not in self._sample_times
                or not 0 <= now - self._sample_times[key] < STALE_TIMEOUT
            )
            for name in ("charging", "discharging"):
                if getattr(self, name) is None:
                    missing.append(name)
            if self.online is not True:
                missing.append("online")
            if self.voltage is None or self.voltage <= 0:
                missing.append("positive_voltage")
            if self.soc is None or not 0 <= self.soc <= 100:
                missing.append("soc_range")
            if any(self.cells.get(index, 0) <= 0 for index in range(1, self.cell_count + 1)):
                missing.append("positive_cells")
            if self.temperature is None:
                missing.append("temperature")
            return missing

    def is_valid(self) -> bool:
        """All configured cells, measurements and explicit permissions are known."""
        return not self.missing_fields()

    @property
    def oldest_sample_time(self) -> float | None:
        """Local monotonic time of the oldest observed physical reading."""
        with self.lock:
            return min(self._sample_times.values(), default=None)

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
