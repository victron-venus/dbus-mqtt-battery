"""
Configuration module for dbus-mqtt-battery.

Supports both config file and CLI arguments:
- Config file: /etc/dbus-mqtt-battery.conf, ~/.config/dbus-mqtt-battery.conf, ./config.ini
- CLI args override config file values

Example config file:
    [mqtt]
    broker = 192.168.1.100
    port = 1883
    topic_prefix = battery

    [battery]
    count = 4
    capacity = 280
    cells_per_bms = 4
    bms_first = 1

    [dbus]
    instance = 512
    service_suffix = mqtt_chain
    product_name = JBD Battery Chain
"""

from __future__ import annotations

import argparse
import configparser
import os
from dataclasses import dataclass, field
from pathlib import Path

# Version
VERSION = "2.6.0"

# Default values
DEFAULT_MQTT_BROKER = "localhost"
DEFAULT_MQTT_PORT = 1883
DEFAULT_BATTERY_COUNT = 4
DEFAULT_INSTALLED_CAPACITY = 280.0
DEFAULT_CELLS_PER_BMS = 4
DEFAULT_BMS_FIRST = 1
DEFAULT_DEVICE_INSTANCE = 512
DEFAULT_SERVICE_SUFFIX = "mqtt_chain"
DEFAULT_PRODUCT_NAME = "JBD Battery Chain"
DEFAULT_TOPIC_PREFIX = "battery"

# Poll interval (milliseconds)
POLL_INTERVAL_MS = 2000

# Stale data timeout (seconds before data considered stale)
STALE_TIMEOUT = 60

# Cells per BMS module
DVCC_CELLS_PER_BMS = DEFAULT_CELLS_PER_BMS

# Config file search paths
CONFIG_FILE_LOCATIONS = [
    "/etc/dbus-mqtt-battery.conf",
    "/data/dbus-mqtt-battery.conf",
    os.path.expanduser("~/.config/dbus-mqtt-battery.conf"),
    os.path.expanduser("~/.config/dbus-mqtt-battery.ini"),
    "config.ini",
    "dbus-mqtt-battery.ini",
]


@dataclass
class MqttConfig:
    """MQTT connection settings."""

    broker: str = DEFAULT_MQTT_BROKER
    port: int = DEFAULT_MQTT_PORT
    topic_prefix: str = DEFAULT_TOPIC_PREFIX


@dataclass
class BatteryConfig:
    """Battery-related settings."""

    count: int = DEFAULT_BATTERY_COUNT
    capacity: float = DEFAULT_INSTALLED_CAPACITY
    cells_per_bms: int = DEFAULT_CELLS_PER_BMS
    bms_first: int = DEFAULT_BMS_FIRST


@dataclass
class DbusConfig:
    """D-Bus service settings."""

    instance: int = DEFAULT_DEVICE_INSTANCE
    service_suffix: str = DEFAULT_SERVICE_SUFFIX
    product_name: str = DEFAULT_PRODUCT_NAME


@dataclass
class Config:
    """Main configuration container."""

    mqtt: MqttConfig = field(default_factory=MqttConfig)
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    dbus: DbusConfig = field(default_factory=DbusConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> Config:
        """Load configuration from INI file."""
        config = configparser.ConfigParser()
        config.read(path)

        mqtt = MqttConfig()
        battery = BatteryConfig()
        dbus = DbusConfig()

        if "mqtt" in config:
            mqtt.broker = config.get("mqtt", "broker", fallback=mqtt.broker)
            mqtt.port = config.getint("mqtt", "port", fallback=mqtt.port)
            mqtt.topic_prefix = config.get("mqtt", "topic_prefix", fallback=mqtt.topic_prefix)

        if "battery" in config:
            battery.count = config.getint("battery", "count", fallback=battery.count)
            battery.capacity = config.getfloat("battery", "capacity", fallback=battery.capacity)
            battery.cells_per_bms = config.getint(
                "battery", "cells_per_bms", fallback=battery.cells_per_bms
            )
            battery.bms_first = config.getint("battery", "bms_first", fallback=battery.bms_first)

        if "dbus" in config:
            dbus.instance = config.getint("dbus", "instance", fallback=dbus.instance)
            dbus.service_suffix = config.get("dbus", "service_suffix", fallback=dbus.service_suffix)
            dbus.product_name = config.get("dbus", "product_name", fallback=dbus.product_name)

        return cls(mqtt=mqtt, battery=battery, dbus=dbus)

    @classmethod
    def find_and_load_config(cls) -> Config:
        """Find and load config from standard locations."""
        for path in CONFIG_FILE_LOCATIONS:
            if os.path.exists(path):
                return cls.from_file(path)
        return cls()


def create_argument_parser() -> argparse.ArgumentParser:
    """Create argument parser with all CLI options."""
    parser = argparse.ArgumentParser(
        description="MQTT to D-Bus Battery Bridge for Victron",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"dbus-mqtt-battery {VERSION}")
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        help="Path to config file (INI format). CLI args override file values.",
    )

    # MQTT settings
    mqtt_group = parser.add_argument_group("MQTT settings")
    mqtt_group.add_argument(
        "--broker", default=argparse.SUPPRESS, help="MQTT broker address (default: localhost)"
    )
    mqtt_group.add_argument(
        "--port", type=int, default=argparse.SUPPRESS, help="MQTT broker port (default: 1883)"
    )
    mqtt_group.add_argument(
        "--topic-prefix", default=argparse.SUPPRESS, help="MQTT topic prefix (default: battery)"
    )

    # Battery settings
    battery_group = parser.add_argument_group("Battery settings")
    battery_group.add_argument(
        "--batteries",
        "--count",
        type=int,
        default=argparse.SUPPRESS,
        dest="battery_count",
        help="Number of batteries (default: 4)",
    )
    battery_group.add_argument(
        "--capacity",
        type=float,
        default=argparse.SUPPRESS,
        help="Installed capacity in Ah (default: 280)",
    )
    battery_group.add_argument(
        "--cells-per-bms",
        type=int,
        default=argparse.SUPPRESS,
        help="Number of cells per BMS module (default: 4)",
    )
    battery_group.add_argument(
        "--bms-first",
        type=int,
        default=argparse.SUPPRESS,
        help="First MQTT BMS index for this chain (default: 1)",
    )

    # D-Bus settings
    dbus_group = parser.add_argument_group("D-Bus settings")
    dbus_group.add_argument(
        "--instance",
        type=int,
        default=argparse.SUPPRESS,
        help="D-Bus device instance (default: 512)",
    )
    dbus_group.add_argument(
        "--service-suffix",
        default=argparse.SUPPRESS,
        help="D-Bus service suffix (default: mqtt_chain)",
    )
    dbus_group.add_argument(
        "--product-name",
        default=argparse.SUPPRESS,
        help="Product name displayed in GUI (default: JBD Battery Chain)",
    )

    return parser


def merge_config_and_args(config: Config, args: argparse.Namespace) -> Config:
    """Merge config file values with CLI arguments (CLI wins)."""
    # MQTT overrides
    if hasattr(args, "broker"):
        config.mqtt.broker = args.broker
    if hasattr(args, "port"):
        config.mqtt.port = args.port
    if hasattr(args, "topic_prefix"):
        config.mqtt.topic_prefix = args.topic_prefix

    # Battery overrides
    if hasattr(args, "battery_count"):
        config.battery.count = args.battery_count
    if hasattr(args, "capacity"):
        config.battery.capacity = args.capacity
    if hasattr(args, "cells_per_bms"):
        config.battery.cells_per_bms = args.cells_per_bms
    if hasattr(args, "bms_first"):
        config.battery.bms_first = args.bms_first

    # D-Bus overrides
    if hasattr(args, "instance"):
        config.dbus.instance = args.instance
    if hasattr(args, "service_suffix"):
        config.dbus.service_suffix = args.service_suffix
    if hasattr(args, "product_name"):
        config.dbus.product_name = args.product_name

    return config


def load_config() -> tuple[Config, argparse.Namespace]:
    """
    Load configuration from file and CLI arguments.

    Returns:
        Tuple of (Config object, parsed args namespace)
    """
    parser = create_argument_parser()
    args = parser.parse_args()

    # Load from config file if specified
    if hasattr(args, "config") and args.config:
        config = Config.from_file(args.config)
    else:
        config = Config.find_and_load_config()

    # Apply non-default CLI args (args with SUPPRESS default won't be in namespace if not provided)
    config = merge_config_and_args(config, args)

    return config, args
