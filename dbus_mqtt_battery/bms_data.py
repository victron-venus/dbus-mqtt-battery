"""
Battery data container for single BMS unit.

Thread-safe container for battery parameters received from MQTT.
"""

from __future__ import annotations

from threading import Lock
from time import time
from typing import Any

# Stale data timeout (seconds)
STALE_TIMEOUT = 60


class BatteryData:
    """Container for single battery data from MQTT."""

    __slots__ = (
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
        self.lock = Lock()

    def update(self, key: str, value: Any) -> None:
        """Update a battery parameter."""
        with self.lock:
            if key == "voltage":
                self.voltage = float(value)
            elif key == "current":
                self.current = float(value)
            elif key == "power":
                self.power = float(value)
            elif key == "soc":
                self.soc = float(value)
            elif key == "capacity_remaining":
                self.capacity_remaining = float(value)
            elif key == "capacity_total":
                self.capacity_total = float(value)
            elif key == "cycles":
                self.cycles = int(float(value))
            elif key == "temperature":
                self.temperature = float(value)
                self.temperatures[1] = float(value)
            elif key.startswith("temperature_"):
                # temperature_1, temperature_2, etc.
                try:
                    temp_idx = int(key.split("_")[1])
                    temp_val = float(value)
                    self.temperatures[temp_idx] = temp_val
                    # Update main temperature as average
                    valid_temps = [t for t in self.temperatures.values() if t > -40]
                    if valid_temps:
                        self.temperature = sum(valid_temps) / len(valid_temps)
                except (TypeError, ValueError, IndexError):
                    pass
            elif key == "charging":
                self.charging = str(value).upper() in ("ON", "TRUE", "1")
            elif key == "discharging":
                self.discharging = str(value).upper() in ("ON", "TRUE", "1")
            elif key == "balancing":
                self.balancing = str(value).upper() in ("ON", "TRUE", "1")
            elif key == "online":
                self.online = str(value).upper() in ("ON", "TRUE", "1")
            elif key.startswith("cell_"):
                # cell_1, cell_2, etc.
                try:
                    cell_idx = int(key.split("_")[1])
                    self.cells[cell_idx] = float(value)
                    self.cell_count = max(self.cell_count, len(self.cells))
                except (TypeError, ValueError, IndexError):
                    pass
            self.last_update = time()

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
        """Check if data is recent enough."""
        return (time() - self.last_update) < STALE_TIMEOUT and self.voltage > 0

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
