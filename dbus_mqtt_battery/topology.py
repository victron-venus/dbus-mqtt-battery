"""Explicit immutable series inventories, separate from legacy configuration."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dvcc import DvccProfile

MAX_MEMBERS = 4
MAX_CELLS_PER_MEMBER = 32
MAX_MANIFEST_BYTES = 262144


@dataclass(frozen=True)
class MemberTopology:
    """One physical member; order in a chain determines stable local cell IDs."""

    bms_id: int
    cells: int
    temperatures: int = 1
    capacity_ah: float | None = None
    max_charge_current: float | None = None
    max_discharge_current: float | None = None


@dataclass(frozen=True)
class ChainTopology:
    """A validated one-source series string with explicitly selected behavior."""

    id: str
    topic_prefix: str
    telemetry_schema: int
    members: tuple[MemberTopology, ...]
    service_suffix: str
    device_instance: int
    product_name: str
    output: str
    protection_profile: DvccProfile | None
    system_voltage_limit: float | None = None
    current_policy: str = "voltage_weighted_bms_estimate"
    soc_policy: str = "minimum_member"
    capacity_policy: str = "minimum_remaining_ah"

    @property
    def member_count(self) -> int:
        """Number of required physical members, including unavailable members."""
        return len(self.members)

    @property
    def total_cells(self) -> int:
        """Total configured cells; telemetry never resizes this inventory."""
        return sum(member.cells for member in self.members)

    @property
    def cell_offsets(self) -> tuple[int, ...]:
        """Zero-based offsets for flattening local member cells into D-Bus cells."""
        return _offsets(tuple(member.cells for member in self.members))

    @property
    def temp_offsets(self) -> tuple[int, ...]:
        """Zero-based temperature offsets, fixed by configuration."""
        return _offsets(tuple(member.temperatures for member in self.members))

    @property
    def installed_capacity(self) -> float | None:
        """Conservative series rated-Ah ceiling, distinct from remaining Ah."""
        capacities = [member.capacity_ah for member in self.members]
        if any(capacity is None for capacity in capacities):
            return None
        return min(capacity for capacity in capacities if capacity is not None)


def _offsets(counts: tuple[int, ...]) -> tuple[int, ...]:
    offset = 0
    result = []
    for count in counts:
        result.append(offset)
        offset += count
    return tuple(result)


def _object(value: Any, name: str, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")  # noqa: TRY004 - config validation contract
    if required - value.keys():
        raise ValueError(f"{name} missing fields: {', '.join(sorted(required - value.keys()))}")
    unknown = value.keys() - required - (optional or set())
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(sorted(map(str, unknown)))}")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _positive(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite positive number")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError(f"{name} must be a finite positive number") from error
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


def _identifier(value: Any, name: str, pattern: str) -> str:
    if not isinstance(value, str) or re.fullmatch(pattern, value, re.ASCII) is None:
        raise ValueError(f"invalid {name}")
    return value


def _member(value: Any) -> MemberTopology:
    row = _object(
        value,
        "member",
        {"bms_id", "cells"},
        {"temperatures", "capacity_ah", "max_charge_current", "max_discharge_current"},
    )
    return MemberTopology(
        bms_id=_integer(row["bms_id"], "bms_id", 1, 65535),
        cells=_integer(row["cells"], "cells", 1, MAX_CELLS_PER_MEMBER),
        temperatures=_integer(row.get("temperatures", 1), "temperatures", 1, 8),
        capacity_ah=_positive(row.get("capacity_ah"), "capacity_ah"),
        max_charge_current=_positive(row.get("max_charge_current"), "max_charge_current"),
        max_discharge_current=_positive(row.get("max_discharge_current"), "max_discharge_current"),
    )


def _protection(value: Any, output: str, members: tuple[MemberTopology, ...]) -> DvccProfile | None:
    if output == "offline":
        if value is not None:
            raise ValueError("offline output requires a null protection_profile")
        return None
    if len({member.cells for member in members}) != 1:
        raise ValueError("managed output currently requires equal cell counts per member")
    if any(
        member.capacity_ah is None
        or member.max_charge_current is None
        or member.max_discharge_current is None
        for member in members
    ):
        raise ValueError("managed members require capacity_ah and both current ratings")
    profile = DvccProfile.from_mapping(value)
    charge = min(
        profile.max_charge_current,
        *(member.max_charge_current for member in members if member.max_charge_current is not None),
    )
    discharge = min(
        profile.max_discharge_current,
        *(
            member.max_discharge_current
            for member in members
            if member.max_discharge_current is not None
        ),
    )
    return replace(
        profile,
        max_charge_current=charge,
        max_discharge_current=discharge,
        min_charge_current=min(
            profile.min_charge_current,
            charge * profile.charge_balance_factor,
            charge * profile.imbalance_aggressive_factor,
        ),
    )


def _chain(value: Any) -> ChainTopology:
    row = _object(
        value,
        "chain",
        {
            "id",
            "connection",
            "source",
            "members",
            "service_suffix",
            "device_instance",
            "aggregation",
            "output",
            "protection_profile",
        },
        {"product_name", "system_voltage_limit"},
    )
    identifier = _identifier(row["id"], "chain id", r"[a-z][a-z0-9_-]{0,63}")
    if row["connection"] != "series":
        raise ValueError("only explicit series connections are supported")
    source = _object(row["source"], "source", {"topic_prefix", "telemetry_schema"})
    prefix = _identifier(
        source["topic_prefix"], "topic_prefix", r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*"
    )
    if len(prefix) > 256:
        raise ValueError("topic_prefix is too long")
    schema = _integer(source["telemetry_schema"], "telemetry_schema", 1, 2)
    if not isinstance(row["members"], list) or not 1 <= len(row["members"]) <= MAX_MEMBERS:
        raise ValueError("a chain requires 1-4 explicitly configured members")
    members = tuple(_member(member) for member in row["members"])
    if len({member.bms_id for member in members}) != len(members):
        raise ValueError("member bms_id values must be unique within a source")
    if schema == 1 and any(member.cells != 4 for member in members):
        raise ValueError("schema 1 requires four cells per member")
    suffix = _identifier(row["service_suffix"], "service_suffix", r"[A-Za-z_][A-Za-z0-9_]{0,63}")
    instance = _integer(row["device_instance"], "device_instance", 0, 65535)
    product = row.get("product_name", identifier)
    if not isinstance(product, str) or not 1 <= len(product) <= 80 or not product.isprintable():
        raise ValueError("product_name must contain 1-80 printable characters")
    policies = _object(row["aggregation"], "aggregation", {"current", "soc", "remaining_capacity"})
    expected = {
        "current": "voltage_weighted_bms_estimate",
        "soc": "minimum_member",
        "remaining_capacity": "minimum_remaining_ah",
    }
    if policies != expected:
        raise ValueError("unsupported aggregation policy; all three policies must be explicit")
    output = row["output"]
    if output not in ("offline", "managed"):
        raise ValueError("output must be offline or managed")
    profile = _protection(row["protection_profile"], output, members)
    voltage_limit = _positive(row.get("system_voltage_limit"), "system_voltage_limit")
    if output == "managed":
        if voltage_limit is None:
            raise ValueError("managed output requires an explicit system_voltage_limit")
        if (
            profile is not None
            and profile.cell_max_voltage * sum(m.cells for m in members) > voltage_limit
        ):
            raise ValueError("configured charge voltage exceeds system_voltage_limit")
    return ChainTopology(
        id=identifier,
        topic_prefix=prefix,
        telemetry_schema=schema,
        members=members,
        service_suffix=suffix,
        device_instance=instance,
        product_name=product,
        output=output,
        protection_profile=profile,
        system_voltage_limit=voltage_limit,
    )


def parse_manifest(document: Any) -> tuple[ChainTopology, ...]:
    """Validate the entire manifest before returning any chain for installation."""
    root = _object(
        document, "manifest", {"config_version", "chains"}, {"reserved_device_instances"}
    )
    _integer(root["config_version"], "config_version", 1, 1)
    if not isinstance(root["chains"], list) or not 1 <= len(root["chains"]) <= 32:
        raise ValueError("manifest requires 1-32 chains")
    reserved = root.get("reserved_device_instances", [514])
    if not isinstance(reserved, list):
        raise ValueError("reserved_device_instances must be an explicit array")  # noqa: TRY004
    reservations = {_integer(value, "reserved device instance", 0, 65535) for value in reserved}
    chains = tuple(_chain(value) for value in root["chains"])
    for field in ("id", "service_suffix", "device_instance", "topic_prefix"):
        if len({getattr(chain, field) for chain in chains}) != len(chains):
            raise ValueError(f"duplicate chain {field}")
    if any(chain.device_instance in reservations for chain in chains):
        raise ValueError("device_instance collides with a reserved device")
    return chains


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_manifest(path: str | Path) -> tuple[ChainTopology, ...]:
    """Load a bounded JSON file; missing/malformed explicit configuration is fatal."""
    with Path(path).open("rb") as handle:
        payload = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("topology manifest is too large")
    return parse_manifest(json.loads(payload, object_pairs_hook=_unique_object))


def parse_topology(document: Any, chain_id: str | None = None) -> ChainTopology:
    """Select one chain after validating every chain in the manifest."""
    return _select(parse_manifest(document), chain_id)


def load_topology(path: str | Path, chain_id: str | None = None) -> ChainTopology:
    """Select one chain from an explicit topology file."""
    return _select(load_manifest(path), chain_id)


def _select(chains: tuple[ChainTopology, ...], chain_id: str | None) -> ChainTopology:
    if chain_id is None:
        if len(chains) != 1:
            raise ValueError("chain id is required for a multi-chain manifest")
        return chains[0]
    for chain in chains:
        if chain.id == chain_id:
            return chain
    raise ValueError(f"unknown configured chain: {chain_id}")
