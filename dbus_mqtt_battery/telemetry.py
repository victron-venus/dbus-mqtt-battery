"""Validate atomic ESPHome telemetry before replacing any live battery data."""

# Exact JSON types intentionally distinguish booleans from Python integer subclasses.
# pylint: disable=unidiomatic-typecheck

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
        if ("seen_mask" in row or "valid_mask" in row) and (
            row.get("seen_mask") != 511 or row.get("valid_mask") != 511
        ):
            raise ValueError("ready frame has incomplete physical evidence")
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
        # The decoder initializes the entire detached model before publication.
        # pylint: disable-next=protected-access
        battery._sample_times = {key: now - age for key in battery._sample_times}
        battery.mark_sample_time(["charging", "discharging", "online"], now - age)
        battery.last_update = now - age
        if not battery.is_valid():
            raise ValueError("invalid or offline BMS in ready frame")
    if len(seen) != battery_count:
        raise ValueError("telemetry is missing a configured BMS")
    return batteries


def merge_partial(
    frame: dict[str, Any],
    previous: dict[int, BatteryData],
    bms_first: int,
    now: float,
    *,
    refresh: bool,
) -> dict[int, BatteryData]:
    """Merge evidenced fields without extending missing-field or new-session ages.

    A prior complete frame is required by the caller. Metadata-free incomplete
    frames retain their original fail-closed semantics. Invalid observed values
    are faults, unlike missing BLE replies. Partial positive MOS status cannot
    remove a previous veto; only a complete frame may do that.
    """
    rows = frame.get("batteries")
    if not isinstance(rows, list):
        raise TypeError("partial frame has no BMS rows")
    result = {slot: battery.copy() for slot, battery in previous.items()}
    found = set()
    names = ("voltage", "current", "soc", "temperature")
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise ValueError("invalid partial BMS id")
        slot = row["id"] - bms_first + 1
        if slot not in result:
            continue
        if slot in found:
            raise ValueError("duplicate partial BMS id")
        found.add(slot)
        battery = result[slot]
        if battery.cell_count != 4:
            raise ValueError("partial schema 1 evidence requires four cells per BMS")
        seen, valid = row.get("seen_mask"), row.get("valid_mask")
        if (
            type(seen) is not int
            or type(valid) is not int
            or not 0 <= seen <= 511
            or not 0 <= valid <= 511
            or valid & ~seen
        ):
            raise ValueError("invalid partial evidence masks")
        if seen != valid:
            raise ValueError("producer observed an invalid physical field")
        age = _number(row, "observed_age_ms") / 1000.0
        if not 0 <= age <= STALE_TIMEOUT or (seen and age >= STALE_TIMEOUT):
            raise ValueError("partial physical evidence expired")
        if not seen:
            continue
        cells = row.get("cells")
        if not isinstance(cells, list) or len(cells) != 4:
            raise ValueError("invalid partial cells")
        for index in range(8):
            if not seen & (1 << index):
                continue
            key = names[index] if index < 4 else f"cell_{index - 3}"
            value = _number(row, key) if index < 4 else _number({key: cells[index - 4]}, key)
            if (index == 0 or index >= 4) and value <= 0:
                raise ValueError("invalid observed voltage")
            if key == "soc" and not 0 <= value <= 100:
                raise ValueError("invalid observed SoC")
            field = "temperature_1" if key == "temperature" else key
            sampled_at = now - age
            if not refresh:
                # Rebooted producers must supply a complete frame before any
                # old physical evidence can gain a new freshness deadline.
                previous_time = battery.oldest_sample_time
                if previous_time is not None:
                    sampled_at = min(sampled_at, previous_time)
            battery.update(key, value)
            battery.mark_sample_time([field], sampled_at)
        if seen & 256:
            for field in ("charging", "discharging"):
                permission = row.get(field)
                if type(permission) is not bool:
                    raise ValueError("invalid observed MOS status")
                if permission is False:
                    battery.update(field, False)
            if refresh:
                battery.mark_sample_time(["charging", "discharging", "online"], now - age)
        if seen == 511 and row.get("online") is not True:
            raise ValueError("complete observed BMS explicitly offline")
    if len(found) != len(previous):
        raise ValueError("partial frame missing a configured BMS row")
    return result
