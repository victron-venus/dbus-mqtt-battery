"""Producer-session and physical-age contracts without a broker or hardware."""

# Pytest fixtures and direct callback calls intentionally exercise the wire boundary.
# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access

from copy import deepcopy
from types import SimpleNamespace

import pytest
from battery_samples import deliver_frame, refresh_battery, telemetry_frame

from dbus_mqtt_battery.mqtt_client import MqttBatteryClient


@pytest.fixture
def receiver(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("dbus_mqtt_battery.bms_data.monotonic", lambda: now[0])
    monkeypatch.setattr("dbus_mqtt_battery.mqtt_client.monotonic", lambda: now[0])
    return now, MqttBatteryClient("unused.invalid", 1883, battery_count=2)


def test_complete_atomic_frame_replaces_all_batteries(receiver):
    _, client = receiver
    deliver_frame(client, telemetry_frame())
    data = client.get_aggregate_data()
    assert data["data_complete"] and data["telemetry_source"] == "atomic"
    assert (data["voltage"], data["current"], data["soc"]) == (26.4, 10, 50)
    assert data["min_cell"] == 3.3
    assert data["allow_discharge"]


def test_retained_snapshots_and_legacy_values_do_not_initialize(receiver):
    _, client = receiver
    client = MqttBatteryClient("unused.invalid", 1883, battery_count=2, telemetry_mode="legacy")
    deliver_frame(client, telemetry_frame(), retain=True)
    client._on_message(
        None,
        None,
        SimpleNamespace(
            topic="battery/sensor/voltage_bms1/state",
            payload=b"13.2",
            retain=True,
        ),
    )
    assert client.get_aggregate_data() is None
    assert client.batteries[1].voltage is None


def test_duplicate_frame_cannot_renew_physical_age(receiver):
    now, client = receiver
    frame = telemetry_frame()
    frame["batteries"][0]["age_ms"] = 10000
    deliver_frame(client, frame)
    assert client.get_aggregate_data()["oldest_sample_time"] == 90
    now[0] = 149
    deliver_frame(client, frame)
    assert client.get_aggregate_data()["data_complete"]
    now[0] = 150
    assert not client.get_aggregate_data()["data_complete"]
    deliver_frame(client, telemetry_frame(seq=2))
    assert client.get_aggregate_data()["data_complete"]


def test_new_boot_does_not_accept_delayed_previous_session(receiver):
    _, client = receiver
    deliver_frame(client, telemetry_frame(seq=10))
    new_boot = telemetry_frame(seq=0, boot_id="new-boot")
    new_boot["batteries"][0]["discharging"] = False
    deliver_frame(client, new_boot)
    assert not client.get_aggregate_data()["allow_discharge"]
    deliver_frame(client, telemetry_frame(seq=11))
    assert not client.get_aggregate_data()["allow_discharge"]


@pytest.mark.parametrize(
    "change",
    [
        lambda frame: frame["batteries"].pop(),
        lambda frame: frame["batteries"][0].pop("discharging"),
        lambda frame: frame["batteries"][0]["cells"].pop(),
        lambda frame: frame["batteries"][0].update(age_ms=60000),
        lambda frame: frame["batteries"][0].update(age_ms=-1),
        lambda frame: frame["batteries"][0].update(voltage=float("nan")),
        lambda frame: frame["batteries"][0].update(soc=101),
        lambda frame: frame["batteries"][0].update(online=False),
        lambda frame: frame["batteries"][0].update(id=2),
        lambda frame: frame.update(ready=False),
    ],
)
def test_bad_or_not_ready_frame_cannot_reuse_previous_good_fields(receiver, change):
    _, client = receiver
    deliver_frame(client, telemetry_frame())
    broken = telemetry_frame(seq=2)
    change(broken)
    deliver_frame(client, broken)
    assert client.get_aggregate_data() is None
    deliver_frame(client, telemetry_frame(seq=1))
    assert client.get_aggregate_data() is None
    deliver_frame(client, telemetry_frame(seq=3))
    assert client.get_aggregate_data()["data_complete"]


def test_disconnection_requires_new_frame_and_cannot_fall_back_to_legacy(receiver):
    _, client = receiver
    deliver_frame(client, telemetry_frame())
    client._on_disconnect(None, None, 1)
    assert client.get_aggregate_data() is None
    deliver_frame(client, telemetry_frame())  # duplicate is not new physical data
    assert client.get_aggregate_data() is None
    client._on_message(
        None,
        None,
        SimpleNamespace(
            topic="battery/sensor/voltage_bms1/state",
            payload=b"13.2",
            retain=False,
        ),
    )
    assert client.batteries[1].voltage is None
    deliver_frame(client, telemetry_frame(seq=2))
    assert client.get_aggregate_data()["data_complete"]


def test_legacy_disconnect_invalidates_measurements_and_latched_permissions(receiver):
    _, client = receiver
    for battery in client.batteries.values():
        refresh_battery(battery)
    assert client.get_aggregate_data()["data_complete"]
    client._on_disconnect(None, None, 1)
    assert client.get_aggregate_data() is None
    assert client.batteries[1].discharging is None


def test_aggregation_keeps_one_snapshot_if_new_frame_arrives_during_calculation(
    receiver, monkeypatch
):
    _, client = receiver
    first = telemetry_frame()
    deliver_frame(client, first)
    calculate = client._compute_electrical_totals
    newer = deepcopy(first)
    newer["seq"] = 2
    for row in newer["batteries"]:
        row.update(voltage=14.0, current=20, soc=90, cells=[3.5] * 4, discharging=False)

    def update_during_calculation(*args):
        deliver_frame(client, newer)
        return calculate(*args)

    monkeypatch.setattr(client, "_compute_electrical_totals", update_during_calculation)
    old = client.get_aggregate_data()
    assert (old["voltage"], old["soc"], old["min_cell"], old["allow_discharge"]) == (
        26.4,
        50,
        3.3,
        True,
    )
    monkeypatch.setattr(client, "_compute_electrical_totals", calculate)
    new = client.get_aggregate_data()
    assert (new["voltage"], new["soc"], new["min_cell"], new["allow_discharge"]) == (
        28,
        90,
        3.5,
        False,
    )


def test_atomic_ids_respect_configured_bms_offset():
    client = MqttBatteryClient("unused.invalid", 1883, battery_count=2, bms_first=3)
    deliver_frame(client, telemetry_frame(first=3))
    assert client.get_aggregate_data()["data_complete"]


def test_default_atomic_mode_ignores_even_nonretained_legacy_cache_republish(receiver):
    _, client = receiver
    assert client.telemetry_source == "atomic"
    client._on_message(
        None,
        None,
        SimpleNamespace(
            topic="battery/sensor/voltage_bms1/state",
            payload=b"13.2",
            retain=False,
        ),
    )
    assert client.batteries[1].voltage is None
    assert client.get_aggregate_data() is None


def test_optional_history_and_temperature_extrema_are_preserved(receiver):
    _, client = receiver
    frame = telemetry_frame()
    frame["batteries"][0].update(capacity=140, cycles=100, temperatures=[25, 55])
    deliver_frame(client, frame)
    data = client.get_aggregate_data()
    assert data["cycles"] == 100
    assert data["max_temp"] == 55
    assert client.batteries[1].capacity_remaining == 140
    frame["seq"] = 2
    frame["batteries"][0].update(capacity=float("nan"), cycles=-1)
    deliver_frame(client, frame)
    assert client.get_aggregate_data()["data_complete"]
    assert client.get_aggregate_data()["cycles"] is None
