"""Complete physical test readings, retaining previously received BMS vetoes."""

# Exercise the same private callback Paho invokes on incoming messages.
# pylint: disable=protected-access

import json
from types import SimpleNamespace


def refresh_battery(battery, *, omit=(), **overrides):
    """Populate one complete sample while preserving any previously received veto."""
    values = {
        "voltage": 13.2,
        "current": 10,
        "power": 132,
        "soc": 50,
        "temperature_1": 25,
        "charging": battery.charging if battery.charging is not None else True,
        "discharging": battery.discharging if battery.discharging is not None else True,
        "online": battery.online if battery.online is not None else True,
        **{f"cell_{index}": 3.3 for index in range(1, battery.cell_count + 1)},
        **overrides,
    }
    for key, value in values.items():
        if key not in omit:
            battery.update(key, value)


def telemetry_frame(*, seq=1, boot_id="test-boot", ready=True, count=2, first=1):
    """Return a complete schema 1 producer frame for the configured BMS range."""
    return {
        "schema": 1,
        "boot_id": boot_id,
        "seq": seq,
        "ready": ready,
        "batteries": [
            {
                "id": index,
                "age_ms": 0,
                "voltage": 13.2,
                "current": 10.0,
                "soc": 50.0,
                "temperature": 25.0,
                "cells": [3.3] * 4,
                "charging": True,
                "discharging": True,
                "online": True,
            }
            for index in range(first, first + count)
        ],
    }


def deliver_frame(client, frame, *, retain=False):
    """Feed serialized wire data through the actual consumer callback."""
    client._on_message(
        None,
        None,
        SimpleNamespace(
            topic=f"{client.topic_prefix}/telemetry",
            payload=json.dumps(frame).encode(),
            retain=retain,
        ),
    )


def partial_frame(*, seq=2, boot_id="test-boot", mask=0, count=2):
    """Drop selected physical evidence from the first BMS of a polling round."""
    frame = telemetry_frame(seq=seq, boot_id=boot_id, count=count, ready=False)
    for row in frame["batteries"]:
        row.update(seen_mask=511, valid_mask=511, observed_age_ms=0)
    row = frame["batteries"][0]
    row.update(seen_mask=mask, valid_mask=mask, observed_age_ms=0 if mask else 60000)
    row.update(age_ms=0 if mask == 511 else 60000, online=mask == 511)
    for bit, field in enumerate(("voltage", "current", "soc", "temperature")):
        if not mask & (1 << bit):
            row[field] = None
    for index in range(4):
        if not mask & (1 << (index + 4)):
            row["cells"][index] = None
    if not mask & 256:
        row.update(charging=None, discharging=None)
    return frame
