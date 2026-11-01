"""Strict complete-frame decoding for explicitly configured series inventories."""

from __future__ import annotations

import json
import math
from typing import Any

from .bms_data import STALE_TIMEOUT, BatteryData
from .topology import ChainTopology, MemberTopology

MAX_FRAME_BYTES = 262144


def required_mask(cell_count: int) -> int:
    """Match producer field numbering, including the gap before cells 5-32."""
    if type(cell_count) is not int or not 1 <= cell_count <= 32:
        raise ValueError("unsupported configured cell count")
    mask = 15 | (1 << 8)
    for cell in range(cell_count):
        mask |= 1 << (4 + cell if cell < 4 else 7 + cell)
    return mask


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def decode_header(payload: str, topology: ChainTopology) -> tuple[dict[str, Any], str, int]:
    """Verify schema/session metadata; caller retains replay and session history."""
    if len(payload) > MAX_FRAME_BYTES:
        raise ValueError("telemetry frame is too large")
    frame = json.loads(payload, object_pairs_hook=_unique_object)
    if (
        not isinstance(frame, dict)
        or type(frame.get("schema")) is not int
        or frame["schema"] != topology.telemetry_schema
    ):
        raise ValueError("telemetry schema does not match configured topology")
    boot_id, sequence = frame.get("boot_id"), frame.get("seq")
    if not isinstance(boot_id, str) or not 1 <= len(boot_id) <= 128:
        raise ValueError("invalid boot_id")
    if type(sequence) is not int or not 0 <= sequence <= 2**64 - 1:
        raise ValueError("invalid sequence")
    if type(frame.get("ready")) is not bool:
        raise ValueError("ready must be a boolean")
    return frame, boot_id, sequence


def _number(value: Any, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"invalid {name}")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"invalid {name}") from error
    if not math.isfinite(result):
        raise ValueError(f"invalid {name}")
    return result


def _inventory(row: dict[str, Any], member: MemberTopology, schema: int) -> list:
    cells = row.get("cells")
    if not isinstance(cells, list) or len(cells) != member.cells:
        raise ValueError("cell array does not match configured inventory")
    expected = required_mask(member.cells)
    for name in ("seen_mask", "valid_mask"):
        if type(row.get(name)) is not int or row[name] != expected:
            raise ValueError("ready frame has incomplete physical evidence")
    if (schema == 2 or "required_mask" in row) and (
        type(row.get("required_mask")) is not int or row["required_mask"] != expected
    ):
        raise ValueError("producer required_mask does not match local inventory")
    if (schema == 2 or "cell_count" in row) and (
        type(row.get("cell_count")) is not int or row["cell_count"] != member.cells
    ):
        raise ValueError("producer cell_count does not match local inventory")
    if (schema == 2 or "reported_cell_count" in row) and (
        _number(row.get("reported_cell_count"), "reported_cell_count") != member.cells
    ):
        raise ValueError("BMS reported_cell_count does not match local inventory")
    return cells


def _measurements(
    row: dict[str, Any], battery: BatteryData, member: MemberTopology, cells: list
) -> None:
    for name in ("voltage", "current", "soc"):
        battery.update(name, _number(row.get(name), name))
    battery.update("temperature", _temperature(row.get("temperature")))
    temperatures = row.get("temperatures")
    if temperatures is not None:
        if not isinstance(temperatures, list) or len(temperatures) != member.temperatures:
            raise ValueError("temperatures do not match configured inventory")
        for index, value in enumerate(temperatures, 1):
            battery.update(f"temperature_{index}", _temperature(value))
    elif member.temperatures != 1:
        raise ValueError("missing configured temperatures")
    for index, value in enumerate(cells, 1):
        voltage = _number(value, "cell voltage")
        if voltage <= 0:
            raise ValueError("cell voltage must be positive")
        battery.update(f"cell_{index}", voltage)
    for name in ("charging", "discharging", "online"):
        if type(row.get(name)) is not bool:
            raise ValueError(f"missing {name} status")
        battery.update(name, row[name])
    for name, target in (("capacity", "capacity_remaining"), ("cycles", "cycles")):
        if name in row:
            try:
                number = _number(row[name], name)
            except ValueError:
                continue
            if number >= 0 and (name != "cycles" or number.is_integer()):
                battery.update(target, number)


def _temperature(value: Any) -> float:
    """Reject a configured sensor fault rather than dropping it from extrema."""
    temperature = _number(value, "temperature")
    if temperature <= -40:
        raise ValueError("configured temperature is outside the valid sensor range")
    return temperature


def decode_batteries(
    frame: dict[str, Any], topology: ChainTopology, now: float
) -> dict[int, BatteryData]:
    """Build detached batteries; incomplete frames never inherit prior readings."""
    if type(frame.get("schema")) is not int or frame["schema"] != topology.telemetry_schema:
        raise ValueError("telemetry schema does not match configured topology")
    if type(frame.get("ready")) is not bool:
        raise ValueError("ready must be a boolean")
    batteries = {
        index: BatteryData(index, member.cells) for index, member in enumerate(topology.members, 1)
    }
    if not frame["ready"]:
        return batteries
    rows = frame.get("batteries")
    if not isinstance(rows, list) or len(rows) != topology.member_count:
        raise ValueError("telemetry must contain exactly the configured members")
    if ("valid_mask" in frame or topology.telemetry_schema == 2) and (
        type(frame.get("valid_mask")) is not int or frame["valid_mask"] != (1 << len(rows)) - 1
    ):
        raise ValueError("telemetry member readiness mask is incomplete")
    expected = {member.bms_id: (index, member) for index, member in enumerate(topology.members, 1)}
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not int:
            raise ValueError("invalid physical BMS id")
        identifier = row["id"]
        if identifier not in expected or identifier in seen:
            raise ValueError("unexpected or duplicate physical BMS id")
        seen.add(identifier)
        slot, member = expected[identifier]
        cells = _inventory(row, member, topology.telemetry_schema)
        age = _number(row.get("age_ms"), "age_ms") / 1000.0
        if not 0 <= age < STALE_TIMEOUT:
            raise ValueError("physical sample is expired")
        battery = batteries[slot]
        _measurements(row, battery, member, cells)
        # Match the original physical observation time, never receipt time.
        # pylint: disable-next=protected-access
        battery._sample_times = {key: now - age for key in battery._sample_times}
        battery.mark_sample_time(["charging", "discharging", "online"], now - age)
        if not battery.is_valid():
            raise ValueError("invalid or offline BMS in ready frame")
    return batteries
