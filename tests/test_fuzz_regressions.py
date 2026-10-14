"""Malformed physical numeric fields must reject the entire telemetry frame."""

import json
from pathlib import Path

import pytest

from dbus_mqtt_battery.telemetry import decode_batteries, decode_header


def test_out_of_range_json_integer_is_a_validation_error():
    seed = Path(__file__).parents[1] / "fuzz/corpus/telemetry/huge-number"
    frame, _, _ = decode_header(seed.read_text())
    with pytest.raises(ValueError, match="invalid voltage"):
        decode_batteries(frame, 1, 1, 4, 10000.0)
    assert json.loads(seed.read_text())["batteries"][0]["voltage"] == 10**400
