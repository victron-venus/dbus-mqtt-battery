"""Complete physical test readings, retaining previously received BMS vetoes."""

import json
from types import SimpleNamespace


def refresh_battery(battery, *, omit=(), **overrides):
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
    client._on_message(
        None,
        None,
        SimpleNamespace(
            topic=f"{client.topic_prefix}/telemetry",
            payload=json.dumps(frame).encode(),
            retain=retain,
        ),
    )
