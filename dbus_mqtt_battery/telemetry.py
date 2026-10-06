"""Validate atomic ESPHome telemetry before replacing any live battery data."""

from __future__ import annotations

import json
import math
from typing import Any

from .bms_data import STALE_TIMEOUT, BatteryData


def decode_header(payload: str) -> tuple[dict[str, Any], str, int]:
    """Only schema 1 frames have the boot/session and sequence freshness contract."""
    frame = json.loads(payload)
    if not isinstance(frame, dict) or type(frame.get("schema")) is not int or frame["schema"] != 1:
        raise ValueError("unsupported telemetry schema")
    boot_id, sequence = frame.get("boot_id"), frame.get("seq")
    if not isinstance(boot_id, str) or not 1 <= len(boot_id) <= 128:
        raise ValueError("invalid boot_id")
    if type(sequence) is not int or sequence < 0:
        raise ValueError("invalid sequence")
    if type(frame.get("ready")) is not bool:
        raise ValueError("ready must be a boolean")
    return frame, boot_id, sequence


def _number(row: dict[str, Any], key: str) -> float:
    value = row.get(key)
    if not isinstance(value, (float, int)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"invalid {key}")
    return float(value)


def decode_batteries(
    frame: dict[str, Any], battery_count: int, bms_first: int, cells_per_bms: int, now: float
) -> dict[int, BatteryData]:
    """Create a complete new snapshot; a bad/missing field cannot mix with old data."""
    batteries = {i: BatteryData(i, cells_per_bms) for i in range(1, battery_count + 1)}
    if not frame["ready"]:
        return batteries
    rows = frame.get("batteries")
    if not isinstance(rows, list) or len(rows) < battery_count:
        raise ValueError("telemetry must contain every configured BMS")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise ValueError("invalid BMS id")
        slot = row["id"] - bms_first + 1
        if slot not in batteries:
            continue
        if slot in seen:
            raise ValueError("duplicate BMS id")
        seen.add(slot)
        age = _number(row, "age_ms") / 1000.0
        if not 0 <= age < STALE_TIMEOUT:
            raise ValueError("physical sample is expired")
        cells = row.get("cells")
        if not isinstance(cells, list) or len(cells) != cells_per_bms:
            raise ValueError("missing configured cell voltages")
        battery = batteries[slot]
        for field in ("voltage", "current", "soc", "temperature"):
            battery.update(field, _number(row, field))
        temperatures = row.get("temperatures")
        if temperatures is not None:
            if not isinstance(temperatures, list) or not temperatures:
                raise ValueError("invalid temperatures array")
            for index, temperature in enumerate(temperatures, 1):
                battery.update(
                    f"temperature_{index}", _number({"temperature": temperature}, "temperature")
                )
        for index, cell in enumerate(cells, 1):
            value = _number({"cell": cell}, "cell")
            if value <= 0:
                raise ValueError("invalid cell voltage")
            battery.update(f"cell_{index}", value)
        for field in ("charging", "discharging", "online"):
            if type(row.get(field)) is not bool:
                raise ValueError(f"missing {field} status")
            battery.update(field, row[field])
        for field, target in (("capacity", "capacity_remaining"), ("cycles", "cycles")):
            if field in row:
                try:
                    value = _number(row, field)
                except (ValueError, TypeError, OverflowError):
                    continue
                if value >= 0 and (field != "cycles" or value.is_integer()):
                    battery.update(target, value)
        # Preserve the sensor's age; receipt time must not rejuvenate old readings.
        battery._sample_times = {key: now - age for key in battery._sample_times}
        battery.last_update = now - age
        if not battery.is_valid():
            raise ValueError("invalid or offline BMS in ready frame")
    if len(seen) != battery_count:
        raise ValueError("telemetry is missing a configured BMS")
    return batteries
