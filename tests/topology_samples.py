"""Explicit topology and producer-format telemetry fixtures."""

from dataclasses import asdict

from dbus_mqtt_battery.telemetry_v2 import required_mask
from dvcc import DvccProfile


def topology_manifest(counts=(4, 8, 4), *, managed=False):
    """A full manifest with physical IDs deliberately unlike local array slots."""
    return {
        "config_version": 1,
        "chains": [
            {
                "id": "test-string",
                "connection": "series",
                "source": {
                    "topic_prefix": "test/string",
                    "telemetry_schema": 1 if all(count == 4 for count in counts) else 2,
                },
                "members": [
                    {
                        "bms_id": 3 + index * 2,
                        "cells": count,
                        "capacity_ah": 70.0,
                        "max_charge_current": 60.0 - index,
                        "max_discharge_current": 90.0 - index,
                    }
                    for index, count in enumerate(counts)
                ],
                "service_suffix": "test_string",
                "device_instance": 544,
                "aggregation": {
                    "current": "voltage_weighted_bms_estimate",
                    "soc": "minimum_member",
                    "remaining_capacity": "minimum_remaining_ah",
                },
                "output": "managed" if managed else "offline",
                "protection_profile": asdict(DvccProfile()) if managed else None,
                "system_voltage_limit": 3.65 * sum(counts) + 1 if managed else None,
            }
        ],
    }


def topology_frame(topology, *, seq=1, ready=True):
    """Match series_telemetry.h bit numbering and generated MQTT JSON fields."""
    return {
        "schema": topology.telemetry_schema,
        "boot_id": "0123456789abcdef",
        "seq": seq,
        "ready": ready,
        "valid_mask": (1 << topology.member_count) - 1 if ready else 0,
        "batteries": [
            {
                "id": member.bms_id,
                "cell_count": member.cells,
                "reported_cell_count": float(member.cells),
                "required_mask": required_mask(member.cells),
                "seen_mask": required_mask(member.cells),
                "valid_mask": required_mask(member.cells),
                "age_ms": 250,
                "observed_age_ms": 250,
                "voltage": 3.3 * member.cells,
                "current": 5.0,
                "soc": 55.0,
                "temperature": 23.0,
                "cells": [3.3] * member.cells,
                "charging": True,
                "discharging": True,
                "online": True,
                "capacity": 38.5,
                "cycles": 12,
            }
            for member in topology.members
        ],
    }
