"""Tests for config loading, CLI args, and dataclass defaults."""

# pylint: disable=missing-class-docstring,missing-function-docstring

import os
import tempfile

from dbus_mqtt_battery.config import (
    CONFIG_FILE_LOCATIONS,
    DEFAULT_BATTERY_COUNT,
    DEFAULT_CELLS_PER_BMS,
    DEFAULT_INSTALLED_CAPACITY,
    DEFAULT_MQTT_BROKER,
    DEFAULT_MQTT_PORT,
    DEFAULT_TOPIC_PREFIX,
    BatteryConfig,
    Config,
    MqttConfig,
    create_argument_parser,
    merge_config_and_args,
)


class TestDataclassDefaults:
    def test_mqtt_defaults(self):
        m = MqttConfig()
        assert m.broker == DEFAULT_MQTT_BROKER
        assert m.port == DEFAULT_MQTT_PORT
        assert m.topic_prefix == DEFAULT_TOPIC_PREFIX

    def test_battery_defaults(self):
        b = BatteryConfig()
        assert b.count == DEFAULT_BATTERY_COUNT
        assert b.capacity == DEFAULT_INSTALLED_CAPACITY
        assert b.cells_per_bms == DEFAULT_CELLS_PER_BMS

    def test_config_file_locations_ordered(self):
        # First entry must be the system-wide Venus OS location
        assert CONFIG_FILE_LOCATIONS[0] == "/etc/dbus-mqtt-battery.conf"


class TestConfigFromFile:
    def test_load_mqtt_section(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write("[mqtt]\nbroker = 192.168.1.99\nport = 8883\n")
            f.flush()
            cfg = Config.from_file(f.name)
        assert cfg.mqtt.broker == "192.168.1.99"
        assert cfg.mqtt.port == 8883
        os.unlink(f.name)

    def test_load_battery_section(self):
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
            f.write("[battery]\ncount = 8\ncapacity = 100.0\n")
            f.flush()
            cfg = Config.from_file(f.name)
        assert cfg.battery.count == 8
        assert cfg.battery.capacity == 100.0
        os.unlink(f.name)

    def test_missing_file_returns_empty(self):
        cfg = Config.from_file("/nonexistent/path/should/fail.ini")
        assert cfg.mqtt.broker == DEFAULT_MQTT_BROKER


class TestCliOverrides:
    def test_argparse_minimal(self):
        parser = create_argument_parser()
        args = parser.parse_args([])
        # config key present but None (SUPPRESS → absent vs None is argparse nuance)
        assert args.config is None

    def test_merge_broker_override(self):
        parser = create_argument_parser()
        args = parser.parse_args(["--broker", "mqtt.example.com"])
        cfg = Config()
        merged = merge_config_and_args(cfg, args)
        assert merged.mqtt.broker == "mqtt.example.com"
        # untouched defaults stay
        assert merged.mqtt.port == DEFAULT_MQTT_PORT

    def test_merge_battery_count(self):
        parser = create_argument_parser()
        args = parser.parse_args(["--batteries", "8"])
        cfg = Config()
        merged = merge_config_and_args(cfg, args)
        assert merged.battery.count == 8

    def test_merge_capacity(self):
        parser = create_argument_parser()
        args = parser.parse_args(["--capacity", "200.5"])
        cfg = Config()
        merged = merge_config_and_args(cfg, args)
        assert merged.battery.capacity == 200.5

    def test_file_then_cli_cli_wins(self):
        """CLI args override file values: merge_config_and_args applies args on top of file-loaded config."""
        parser = create_argument_parser()
        # Simulate: file loaded count=8, CLI passes --batteries 4
        args = parser.parse_args(["--batteries", "4"])
        cfg = Config()
        cfg.battery.count = 8  # pre-loaded from file
        merged = merge_config_and_args(cfg, args)
        assert merged.battery.count == 4


class TestAlarmDefaults:
    def test_alarm_low_soc_default(self):
        b = BatteryConfig()
        assert b.alarm_low_soc == 10
        assert b.alarm_low_soc_critical == 5

    def test_alarm_cell_voltage_defaults(self):
        b = BatteryConfig()
        assert b.alarm_low_cell_voltage == 2.9
        assert b.alarm_high_cell_voltage == 3.55
        assert b.alarm_cell_imbalance == 0.1

    def test_alarm_temp_defaults(self):
        b = BatteryConfig()
        assert b.alarm_high_temp == 45
        assert b.alarm_high_temp_critical == 55
        assert b.alarm_low_temp == 0
        assert b.alarm_low_temp_critical == -10
