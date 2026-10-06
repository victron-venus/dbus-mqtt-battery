"""Contract tests for MqttBatteryClient — no Venus/MQTT broker needed."""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,import-outside-toplevel

import sys
import types
from unittest.mock import MagicMock, patch

# Stub paho before importing the module under test
_paho = types.ModuleType("paho.mqtt.client")
vars(_paho)["Client"] = MagicMock
vars(_paho)["client"] = types.ModuleType("paho.mqtt.client")
vars(_paho)["client"].Client = MagicMock
vars(_paho)["enums"] = types.ModuleType("paho.mqtt.enums")
vars(_paho)["enums"].CallbackAPIVersion = types.SimpleNamespace(VERSION1=1)
sys.modules["paho.mqtt"] = _paho
sys.modules["paho.mqtt.client"] = vars(_paho)["client"]
sys.modules["paho.mqtt.enums"] = vars(_paho)["enums"]

from battery_samples import refresh_battery

from dbus_mqtt_battery.mqtt_client import MqttBatteryClient


def make_client(**kwargs):
    defaults = {
        "broker": "localhost",
        "port": 1883,
        "battery_count": 2,
        "topic_prefix": "battery",
        "telemetry_mode": "legacy",
    }
    defaults.update(kwargs)
    return MqttBatteryClient(**defaults)


def test_missing_required_temperature_keeps_measurements_unknown():
    client = make_client(battery_count=1)
    battery = client.batteries[1]
    refresh_battery(battery, omit=("temperature_1",))
    assert not battery.is_valid()
    assert not battery.temperatures
    data = client.get_aggregate_data()
    assert data is None
    battery.update("temperature", 27.0)
    data = client.get_aggregate_data()
    assert data["min_temp"] == data["max_temp"] == 27.0
    assert data["min_temp_id"] == data["max_temp_id"] == 1


class TestMqttBatteryClientInit:
    """Sanity checks for client construction."""

    def test_creates_battery_slots(self):
        client = make_client(battery_count=4)
        assert len(client.batteries) == 4
        assert list(client.batteries.keys()) == [1, 2, 3, 4]

    def test_bms_first_clamped_to_1(self):
        client = make_client(bms_first=0)
        assert client.bms_first == 1
        client = make_client(bms_first=-5)
        assert client.bms_first == 1

    def test_client_id_uses_pid(self):
        import os

        with patch("dbus_mqtt_battery.mqtt_client.mqtt.Client") as constructor:
            make_client(topic_prefix="batt")
        assert str(os.getpid()) in constructor.call_args.kwargs["client_id"]


class TestOnMessageParsing:
    """MQTT message → battery update contract."""

    def _msg(self, topic, payload):
        msg = MagicMock()
        msg.topic = topic
        msg.payload = payload.encode()
        msg.retain = False
        return msg

    def test_voltage_bms1_updates_slot_1(self):
        client = make_client(battery_count=2)
        client._on_message(None, None, self._msg("battery/sensor/voltage_bms1/state", "12.5"))
        assert client.batteries[1].voltage == 12.5

    def test_voltage_bms2_updates_slot_2(self):
        client = make_client(battery_count=2)
        client._on_message(None, None, self._msg("battery/sensor/voltage_bms2/state", "13.1"))
        assert client.batteries[2].voltage == 13.1

    def test_chain_offset(self):
        # chain2 starts at bms3 → internal slot 1
        client = make_client(battery_count=2, bms_first=3)
        client._on_message(None, None, self._msg("battery/sensor/voltage_bms3/state", "12.5"))
        assert client.batteries[1].voltage == 12.5

    def test_out_of_range_bms_index_ignored(self):
        client = make_client(battery_count=2)
        client._on_message(None, None, self._msg("battery/sensor/voltage_bms9/state", "12.5"))
        assert client.batteries[1].voltage is None
        assert client.batteries[2].voltage is None

    def test_soc_message(self):
        client = make_client()
        client._on_message(None, None, self._msg("battery/sensor/soc_bms1/state", "75.5"))
        assert client.batteries[1].soc == 75.5

    def test_cell_voltage_message(self):
        client = make_client()
        client._on_message(None, None, self._msg("battery/sensor/voltage_cell1_bms1/state", "3.30"))
        assert client.batteries[1].cells[1] == 3.30

    def test_temperature_message(self):
        client = make_client()
        client._on_message(None, None, self._msg("battery/sensor/temperature1_bms1/state", "25.0"))
        assert client.batteries[1].temperatures[1] == 25.0

    def test_balancing_on_message(self):
        client = make_client()
        client._on_message(
            None, None, self._msg("battery/binary_sensor/balancing_bms1/state", "ON")
        )
        assert client.batteries[1].balancing is True

    def test_invalid_topic_ignored(self):
        client = make_client()
        client._on_message(None, None, self._msg("battery", ""))
        assert client.batteries[1].voltage is None


class TestTotals:
    """Aggregate total message handling."""

    def _msg(self, topic, payload):
        msg = MagicMock()
        msg.topic = topic
        msg.payload = payload.encode()
        msg.retain = False
        return msg

    def test_voltage_total(self):
        client = make_client()
        client._on_message(None, None, self._msg("battery/sensor/voltage_total/state", "48.0"))
        assert client.total_voltage == 48.0

    def test_current_total_sets_flag(self):
        client = make_client()
        client._on_message(None, None, self._msg("battery/sensor/current_total/state", "10.5"))
        assert client.current_total_seen is True
        assert client.total_current == 10.5

    def test_soc_total_sets_flag(self):
        client = make_client()
        client._on_message(None, None, self._msg("battery/sensor/soc_total/state", "80.0"))
        assert client.soc_total_seen is True
        assert client.total_soc == 80.0

    def test_non_numeric_ignored(self):
        client = make_client()
        client._on_message(
            None, None, self._msg("battery/sensor/voltage_total/state", "not-a-number")
        )
        assert client.total_voltage == 0.0


class TestGetAggregateData:
    """get_aggregate_data() contract."""

    def test_returns_none_when_no_batteries_valid(self):
        client = make_client(battery_count=2)
        result = client.get_aggregate_data()
        assert result is None

    def test_returns_dict_when_batteries_present(self):
        client = make_client(battery_count=1)
        refresh_battery(client.batteries[1], voltage=12.5, current=5.0, soc=80.0)
        result = client.get_aggregate_data()
        assert result is not None
        assert result["voltage"] == 12.5
        assert result["current"] == 5.0
        assert result["soc"] == 80.0
        assert "min_cell" in result
        assert "max_cell" in result
        assert "allow_charge" in result
        assert "allow_discharge" in result


class TestAggregateFreshness:
    """Each aggregate topic must expire independently of other MQTT traffic."""

    def test_stale_current_falls_back_despite_fresh_voltage(self, monkeypatch):
        client = make_client(battery_count=1)
        now = [100.0]
        monkeypatch.setattr("dbus_mqtt_battery.mqtt_client.monotonic", lambda: now[0])
        client._update_total("voltage_total", "52")
        client._update_total("current_total", "20")
        now[0] += 60
        client._update_total("voltage_total", "52")
        battery = client.batteries[1]
        refresh_battery(battery, voltage=52, current=-5, power=-260, soc=70)
        result = client.get_aggregate_data()
        assert result["current"] == -5
        assert result["power"] == -260
        client._update_total("current_total", "2")
        assert client.get_aggregate_data()["current"] == 2

    def test_all_fields_expire_independently(self, monkeypatch):
        client = make_client(battery_count=1)
        now = [100.0]
        monkeypatch.setattr("dbus_mqtt_battery.mqtt_client.monotonic", lambda: now[0])
        for name, value in {
            "voltage": 54,
            "current": 10,
            "power": 540,
            "soc": 90,
            "capacity": 252,
        }.items():
            client._update_total(f"{name}_total", str(value))
        baseline = [{"voltage": 52, "current": -5, "power": -260, "soc": 70}]
        assert client._compute_electrical_totals(baseline, 196) == (54, 10, 540, 90, 252)
        now[0] += 59
        assert client._compute_electrical_totals(baseline, 196) == (54, 10, 540, 90, 252)
        now[0] += 1
        client._update_total("soc_total", "80")
        assert client._compute_electrical_totals(baseline, 196) == (52, -5, -260, 80, 196)

    def test_unknown_and_invalid_totals_do_not_refresh_readings(self, monkeypatch):
        client = make_client(battery_count=1)
        now = [100.0]
        monkeypatch.setattr("dbus_mqtt_battery.mqtt_client.monotonic", lambda: now[0])
        client._update_total("current_total", "20")
        now[0] += 60
        for value in ("invalid", "nan", "inf", "-inf"):
            client._update_total("current_total", value)
        client._update_total("unrecognized_total", "123")
        baseline = [{"voltage": 52, "current": -5, "power": -260, "soc": 70}]
        assert client._compute_electrical_totals(baseline, 196) == (52, -5, -260, 70, 196)

    def test_fresh_zero_totals_are_valid(self, monkeypatch):
        client = make_client(battery_count=1)
        monkeypatch.setattr("dbus_mqtt_battery.mqtt_client.monotonic", lambda: 100.0)
        for name in ("current", "power", "soc", "capacity"):
            client._update_total(f"{name}_total", "0")
        baseline = [{"voltage": 52, "current": -5, "power": -260, "soc": 70}]
        assert client._compute_electrical_totals(baseline, 196) == (52, 0, 0, 0, 0)


class TestTemperatureGlobalIds:
    """MQTTBATT-1: temperature global IDs use configured temps_per_bms stride.

    Identities must stay collision-free and stable across mixed sensor counts,
    arrival order, and a BMS going offline then rejoining — never recompute
    stride from only the currently valid battery set. Undersized stride withholds
    IDs (no silent renumber) while still exporting temperature extrema values.
    """

    @staticmethod
    def _collect(client, valid_batts):
        cells, temps, values, unambiguous = client._collect_cells_and_temps(valid_batts)
        return cells, temps, values, unambiguous

    @staticmethod
    def _ids(temps):
        return [gid for gid, _ in temps]

    def test_four_temps_per_bms_unique_global_ids(self):
        client = make_client(temps_per_bms=4)
        valid_batts = [
            {
                "battery_id": 1,
                "cells": {},
                "temperatures": {1: 20.0, 2: 21.0, 3: 22.0, 4: 23.0},
            },
            {
                "battery_id": 2,
                "cells": {},
                "temperatures": {1: 24.0, 2: 25.0, 3: 26.0, 4: 27.0},
            },
        ]
        _cells, temps, values, ok = self._collect(client, valid_batts)
        ids = self._ids(temps)
        assert ok is True
        assert ids == [1, 2, 3, 4, 5, 6, 7, 8]
        assert len(ids) == len(set(ids))
        assert min(values) == 20.0 and max(values) == 27.0

    def test_two_temps_per_bms_keeps_legacy_stride(self):
        client = make_client(temps_per_bms=2)
        valid_batts = [
            {"battery_id": 1, "cells": {}, "temperatures": {1: 20.0, 2: 21.0}},
            {"battery_id": 2, "cells": {}, "temperatures": {1: 24.0, 2: 25.0}},
        ]
        _cells, temps, values, ok = self._collect(client, valid_batts)
        assert ok is True
        assert self._ids(temps) == [1, 2, 3, 4]
        assert min(values) == 20.0 and max(values) == 25.0

    def test_default_undersized_stride_withholds_ids_keeps_extrema(self):
        """Supervisor repro: default temps_per_bms=2 with BMS1 indices 1..4 + BMS2 1..2.

        Previously emitted duplicate global IDs 3/4. Must not renumber; withhold IDs
        and still report min/max temperature values from every sensor via the real
        get_aggregate_data export path (BatteryData.update live fields).
        """
        client = make_client()  # default temps_per_bms=2
        assert client.temps_per_bms == 2

        def seed(battery_id: int, temps: dict[int, float]) -> None:
            b = client.batteries[battery_id]
            refresh_battery(b, current=1.0, power=13.2, soc=80.0)
            for idx, temp in temps.items():
                b.update(f"temperature_{idx}", temp)

        seed(1, {1: 20.0, 2: 21.0, 3: 10.5, 4: 30.0})
        seed(2, {1: 24.0, 2: 25.0})
        assert client.batteries[1].is_valid()
        assert client.batteries[2].is_valid()

        data = client.get_aggregate_data()
        assert data is not None
        assert data["min_temp"] == 10.5
        assert data["max_temp"] == 30.0
        assert data["min_temp_id"] is None
        assert data["max_temp_id"] is None

        # Collect path agrees: IDs withheld, values retained.
        snapshots = [
            {
                "battery_id": 1,
                "cells": {},
                "temperatures": dict(client.batteries[1].temperatures),
            },
            {
                "battery_id": 2,
                "cells": {},
                "temperatures": dict(client.batteries[2].temperatures),
            },
        ]
        _cells, temps, values, ok = self._collect(client, snapshots)
        assert ok is False
        assert temps == []
        assert sorted(values) == [10.5, 20.0, 21.0, 24.0, 25.0, 30.0]

    def test_mixed_sensor_counts_stable_ids(self):
        """BMS1 denser than BMS2: BMS2 keeps stride-sized slots (5,6 with stride 4)."""
        client = make_client(temps_per_bms=4)
        both = [
            {
                "battery_id": 1,
                "cells": {},
                "temperatures": {1: 20.0, 2: 21.0, 3: 22.0, 4: 23.0},
            },
            {"battery_id": 2, "cells": {}, "temperatures": {1: 24.0, 2: 25.0}},
        ]
        _cells, temps, _values, ok = self._collect(client, both)
        assert ok is True
        by_id = {gid: val for gid, val in temps}
        assert by_id[1] == 20.0 and by_id[4] == 23.0
        assert by_id[5] == 24.0 and by_id[6] == 25.0
        assert self._ids(temps) == [1, 2, 3, 4, 5, 6]

    def test_bms_offline_does_not_renumber_remaining(self):
        """Supervisor repro: after BMS1 drops out, BMS2 must keep IDs 5,6 not 3,4."""
        client = make_client(temps_per_bms=4)
        both = [
            {
                "battery_id": 1,
                "cells": {},
                "temperatures": {1: 20.0, 2: 21.0, 3: 22.0, 4: 23.0},
            },
            {"battery_id": 2, "cells": {}, "temperatures": {1: 24.0, 2: 25.0}},
        ]
        _c, temps_both, _, ok1 = self._collect(client, both)
        only_bms2 = [both[1]]
        _c, temps_one, _, ok2 = self._collect(client, only_bms2)
        assert ok1 and ok2
        assert self._ids(temps_both) == [1, 2, 3, 4, 5, 6]
        assert self._ids(temps_one) == [5, 6]
        assert dict(temps_one) == {5: 24.0, 6: 25.0}

    def test_arrival_order_does_not_change_ids(self):
        """Sparse BMS2 first, then dense BMS1: configured stride keeps BMS2 at 5,6."""
        client = make_client(temps_per_bms=4)
        bms2_only = [
            {"battery_id": 2, "cells": {}, "temperatures": {1: 24.0, 2: 25.0}},
        ]
        both = [
            {
                "battery_id": 1,
                "cells": {},
                "temperatures": {1: 20.0, 2: 21.0, 3: 22.0, 4: 23.0},
            },
            {"battery_id": 2, "cells": {}, "temperatures": {1: 24.0, 2: 25.0}},
        ]
        _c, first, _, _ = self._collect(client, bms2_only)
        _c, second, _, _ = self._collect(client, both)
        assert self._ids(first) == [5, 6]
        assert dict(first) == {5: 24.0, 6: 25.0}
        assert dict(second)[5] == 24.0 and dict(second)[6] == 25.0

    def test_bms_rejoin_keeps_same_ids(self):
        client = make_client(temps_per_bms=4)
        both = [
            {
                "battery_id": 1,
                "cells": {},
                "temperatures": {1: 20.0, 2: 21.0, 3: 22.0, 4: 23.0},
            },
            {"battery_id": 2, "cells": {}, "temperatures": {1: 24.0, 2: 25.0}},
        ]
        only2 = [both[1]]
        _c, a, _, _ = self._collect(client, both)
        _c, b, _, _ = self._collect(client, only2)
        _c, c, _, _ = self._collect(client, both)
        assert self._ids(a) == self._ids(c) == [1, 2, 3, 4, 5, 6]
        assert self._ids(b) == [5, 6]
