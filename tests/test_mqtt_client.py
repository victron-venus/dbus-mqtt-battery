"""Contract tests for MqttBatteryClient — no Venus/MQTT broker needed."""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access,import-outside-toplevel

import sys
import types
from unittest.mock import MagicMock

# Stub paho before importing the module under test
_paho = types.ModuleType("paho.mqtt.client")
_paho.Client = MagicMock
_paho.client = types.ModuleType("paho.mqtt.client")
_paho.client.Client = MagicMock
_paho.enums = types.ModuleType("paho.mqtt.enums")
_paho.enums.CallbackAPIVersion = types.SimpleNamespace(VERSION1=1)
sys.modules["paho.mqtt"] = _paho
sys.modules["paho.mqtt.client"] = _paho.client
sys.modules["paho.mqtt.enums"] = _paho.enums

from dbus_mqtt_battery.mqtt_client import MqttBatteryClient


def make_client(**kwargs):
    defaults = {"broker": "localhost", "port": 1883, "battery_count": 2, "topic_prefix": "battery"}
    defaults.update(kwargs)
    return MqttBatteryClient(**defaults)


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

        client = make_client(topic_prefix="batt")
        # client.client is the paho Client; verify pid appears in constructed client_id string
        cid = client.client._client_id
        assert str(os.getpid()) in (cid.decode() if isinstance(cid, bytes) else cid)


class TestOnMessageParsing:
    """MQTT message → battery update contract."""

    def _msg(self, topic, payload):
        msg = MagicMock()
        msg.topic = topic
        msg.payload = payload.encode()
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
        assert client.batteries[1].voltage == 0.0
        assert client.batteries[2].voltage == 0.0

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
        assert client.batteries[1].voltage == 0.0


class TestTotals:
    """Aggregate total message handling."""

    def _msg(self, topic, payload):
        msg = MagicMock()
        msg.topic = topic
        msg.payload = payload.encode()
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
        # is_valid() requires voltage > 0; trigger freshness check
        client.batteries[1].update("voltage", "12.5")
        client.batteries[1].update("current", "5.0")
        client.batteries[1].update("soc", "80.0")
        result = client.get_aggregate_data()
        assert result is not None
        assert result["voltage"] == 12.5
        assert result["current"] == 5.0
        assert result["soc"] == 80.0
        assert "min_cell" in result
        assert "max_cell" in result
        assert "allow_charge" in result
        assert "allow_discharge" in result
