#!/usr/bin/python3
"""
dbus-mqtt-battery - MQTT to D-Bus Bridge for JBD BMS via ESP32
=============================================================

Receives battery data from ESP32 (ESPHome) via MQTT and publishes to Victron D-Bus.
Fully compatible with Victron GUI v2.

Architecture:
    [JBD BMS] <--BLE--> [ESP32 + ESPHome] <--MQTT--> [This Script] --> D-Bus --> Victron GX

MQTT Topics (from ESPHome with topic_prefix: battery):
    battery/sensor/voltage_bms1/state
    battery/sensor/current_bms1/state
    battery/sensor/soc_bms1/state
    battery/sensor/capacity_remaining_bms1/state
    battery/sensor/voltage_cell1_bms1/state
    battery/sensor/voltage_total/state
    battery/sensor/current_total/state
    ...

Usage:
    ./dbus-mqtt-battery.py --broker 192.168.160.150 --batteries 4
"""

from __future__ import annotations

import logging
import os
import sys
from time import sleep, time
from typing import Any

# Add Victron library path
sys.path.insert(
    1,
    os.path.join(
        os.path.dirname(__file__),
        "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python",
    ),
)

from vedbus import VeDbusService

# Import from package (replaces duplicated BatteryData and MqttBatteryClient)
from dbus_mqtt_battery import (
    DVCC_CELLS_PER_BMS,
    PATH_DC_CURRENT,
    PATH_DC_POWER,
    PATH_DC_VOLTAGE,
    PATH_TIME_TO_GO,
    POLL_INTERVAL_MS,
    STALE_TIMEOUT,
    VERSION,
    Config,
    MqttBatteryClient,
    create_poll_function,
    get_bus,
    load_config,
    register_signal_handlers,
    run_main_loop,
    setup_dbus_paths_alarms,
    setup_dbus_paths_common,
    setup_dbus_paths_dc,
    setup_main_loop,
)

# First-party imports (local modules)
from dvcc import (
    DVCC_CELL_MAX_VOLTAGE,
    DVCC_MAX_CHARGE_CURRENT,
    DVCC_MAX_DISCHARGE_CURRENT,
    DVCC_MIN_CHARGE_CURRENT,
    DvccController,
)

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("MqttBattery")

# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_MQTT_BROKER = "localhost"
DEFAULT_MQTT_PORT = 1883


# D-Bus alarm paths (values: 0 = OK, 1 = Warning, 2 = Alarm/Critical)
ALARM_PATH_LOW_SOC = "/Alarms/LowSoc"
ALARM_PATH_LOW_CELL_VOLTAGE = "/Alarms/LowCellVoltage"
ALARM_PATH_HIGH_CELL_VOLTAGE = "/Alarms/HighCellVoltage"
ALARM_PATH_CELL_IMBALANCE = "/Alarms/CellImbalance"
ALARM_PATH_HIGH_TEMPERATURE = "/Alarms/HighTemperature"
ALARM_PATH_LOW_TEMPERATURE = "/Alarms/LowTemperature"
ALARM_PATH_INTERNAL_FAILURE = "/Alarms/InternalFailure"
ALARM_PATH_LOW_VOLTAGE = "/Alarms/LowVoltage"
ALARM_PATH_HIGH_VOLTAGE = "/Alarms/HighVoltage"

# Alias for backward compatibility
CELLS_PER_BMS = DVCC_CELLS_PER_BMS


def _gettext_fmt(fmt: str):
    """Build a gettextcallback rendering values with fmt (e.g. "%.1fAh"); empty when falsy."""
    return lambda _a, x: (fmt % x) if x else ""


# =============================================================================
# D-BUS SERVICE
# =============================================================================


class DbusAggregateService:
    """D-Bus service for aggregate battery (GUI v2 compatible)"""

    def __init__(
        self,
        mqtt_client: MqttBatteryClient,
        device_instance: int = 512,
        service_suffix: str = "mqtt_chain",
        product_name: str = "JBD Battery Chain",
        config: Config = None,
    ):
        self.mqtt = mqtt_client
        self.device_instance = device_instance
        self.product_name = product_name
        self.config = config if config is not None else Config()

        # Initialize DVCC controller for dynamic current limiting
        total_cells = mqtt_client.battery_count * mqtt_client.cells_per_bms
        self.dvcc = DvccController(total_cells, mqtt_client.battery_count)
        self.dvcc_log_interval = 30  # Log DVCC status every N seconds
        self.last_dvcc_log = 0
        self._comm_alarm_active = False  # For log-on-transition of CommunicationError

        service_name = f"com.victronenergy.battery.{service_suffix}"
        self._dbusservice = VeDbusService(service_name, get_bus(), register=False)

        self._setup_paths()
        self._dbusservice.register()
        logger.info("D-Bus service registered: %s", service_name)
        logger.info(
            "DVCC enabled: %d cells, CVL=%.1fV, CCL max=%sA, DCL max=%sA",
            total_cells,
            DVCC_CELL_MAX_VOLTAGE * total_cells,
            DVCC_MAX_CHARGE_CURRENT,
            DVCC_MAX_DISCHARGE_CURRENT,
        )

    def _setup_paths(self):
        """Setup D-Bus paths for Victron GUI v2 compatibility"""

        # Common paths (management, device identification)
        setup_dbus_paths_common(
            self._dbusservice,
            process_name=__file__,
            version=VERSION,
            connection="MQTT ESP32",
            device_instance=self.device_instance,
            product_name=self.product_name,
            hardware_version="ESP32 BLE Proxy",
        )

        # DC measurements
        setup_dbus_paths_dc(self._dbusservice, include_formats=True)

        # State of charge
        self._dbusservice.add_path("/Soc", None, writeable=True)
        self._dbusservice.add_path(
            "/Capacity",
            None,
            writeable=True,
            gettextcallback=_gettext_fmt("%.1fAh"),
        )
        self._dbusservice.add_path(
            "/InstalledCapacity",
            None,
            writeable=True,
            gettextcallback=_gettext_fmt("%.0fAh"),
        )
        self._dbusservice.add_path(
            "/ConsumedAmphours",
            None,
            writeable=True,
            gettextcallback=_gettext_fmt("%.1fAh"),
        )

        # Battery system configuration (GUI v2 System menu)
        self._dbusservice.add_path("/System/NrOfBatteries", self.mqtt.battery_count, writeable=True)
        self._dbusservice.add_path("/System/BatteriesParallel", 1, writeable=True)
        self._dbusservice.add_path(
            "/System/BatteriesSeries", self.mqtt.battery_count, writeable=True
        )
        self._dbusservice.add_path(
            "/System/NrOfCellsPerBattery", self.mqtt.cells_per_bms, writeable=True
        )

        # Cell voltages (GUI v2)
        self._dbusservice.add_path(
            "/System/MinCellVoltage",
            None,
            writeable=True,
            gettextcallback=_gettext_fmt("%.3fV"),
        )
        self._dbusservice.add_path("/System/MinVoltageCellId", None, writeable=True)
        self._dbusservice.add_path(
            "/System/MaxCellVoltage",
            None,
            writeable=True,
            gettextcallback=_gettext_fmt("%.3fV"),
        )
        self._dbusservice.add_path("/System/MaxVoltageCellId", None, writeable=True)
        self._dbusservice.add_path(
            "/Voltages/Sum",
            None,
            writeable=True,
            gettextcallback=_gettext_fmt("%.2fV"),
        )
        self._dbusservice.add_path(
            "/Voltages/Diff",
            None,
            writeable=True,
            gettextcallback=_gettext_fmt("%.3fV"),
        )

        # Individual cell voltages for GUI v2
        # Total cells = battery_count × cells_per_battery
        total_cells = self.mqtt.battery_count * self.mqtt.cells_per_bms

        # Add /System/NrOfCells - required for GUI v2 to know how many cells to display
        self._dbusservice.add_path("/System/NrOfCells", total_cells, writeable=True)

        # GUI v2 standard paths: /Cell/{i}/Voltage (0-indexed)
        for i in range(total_cells):
            self._dbusservice.add_path(
                f"/Cell/{i}/Voltage",
                None,
                writeable=True,
                gettextcallback=_gettext_fmt("%.3fV"),
            )
            # Balancing status per cell (for color coding in GUI)
            self._dbusservice.add_path(f"/Cell/{i}/Balance", None, writeable=True)

        # Legacy paths for backward compatibility (dbus-serialbattery format: /Voltages/Cell1..CellN)
        for i in range(1, total_cells + 1):
            self._dbusservice.add_path(
                f"/Voltages/Cell{i}",
                None,
                writeable=True,
                gettextcallback=_gettext_fmt("%.3fV"),
            )
            self._dbusservice.add_path(f"/Balances/Cell{i}", None, writeable=True)

        # Temperature sensors (dbus-serialbattery format)
        # Temperature1..4 = individual battery temperatures
        # Note: /Dc/0/Temperature is already added above
        for i in range(1, 5):  # Temperature1..Temperature4
            self._dbusservice.add_path(f"/System/Temperature{i}", None, writeable=True)
            self._dbusservice.add_path(f"/System/Temperature{i}Name", f"BMS {i}", writeable=True)
        self._dbusservice.add_path("/System/MOSTemperature", None, writeable=True)

        # Min/Max temperature with IDs
        self._dbusservice.add_path("/System/MinCellTemperature", None, writeable=True)
        self._dbusservice.add_path("/System/MinTemperatureCellId", None, writeable=True)
        self._dbusservice.add_path("/System/MaxCellTemperature", None, writeable=True)
        self._dbusservice.add_path("/System/MaxTemperatureCellId", None, writeable=True)

        # Battery modules
        self._dbusservice.add_path("/System/NrOfModulesOnline", None, writeable=True)
        self._dbusservice.add_path("/System/NrOfModulesOffline", None, writeable=True)
        self._dbusservice.add_path("/System/NrOfModulesBlockingCharge", None, writeable=True)
        self._dbusservice.add_path("/System/NrOfModulesBlockingDischarge", None, writeable=True)

        # History
        self._dbusservice.add_path("/History/ChargeCycles", None, writeable=True)
        self._dbusservice.add_path(PATH_TIME_TO_GO, None, writeable=True)

        # Charge/discharge control (DVCC) - default values for 4S LiFePO4
        # CVL = 3.65V × 4 cells × 4 batteries = 58.4V (series config)
        # CCL/DCL = typical limits for 280Ah LiFePO4
        self._dbusservice.add_path(
            "/Info/MaxChargeCurrent",
            100.0,
            writeable=True,
            gettextcallback=_gettext_fmt("%.1fA"),
        )
        self._dbusservice.add_path(
            "/Info/MaxDischargeCurrent",
            120.0,
            writeable=True,
            gettextcallback=_gettext_fmt("%.1fA"),
        )
        self._dbusservice.add_path(
            "/Info/MaxChargeVoltage",
            58.4,
            writeable=True,
            gettextcallback=_gettext_fmt("%.2fV"),
        )
        self._dbusservice.add_path(
            "/Info/MaxChargeCellVoltage",
            3.65,
            writeable=True,
            gettextcallback=_gettext_fmt("%.3fV"),
        )

        # IO
        self._dbusservice.add_path("/Io/AllowToCharge", 1, writeable=True)
        self._dbusservice.add_path("/Io/AllowToDischarge", 1, writeable=True)
        self._dbusservice.add_path("/Io/AllowToBalance", 1, writeable=True)

        # Alarms
        setup_dbus_paths_alarms(self._dbusservice)

        # Reliability: stale data indicator (0=fresh, 1=stale)
        self._dbusservice.add_path("/System/StaleData", 0, writeable=True)

    def _set_communication_error(self, stale: bool) -> None:
        """Update /Alarms/CommunicationError and /System/StaleData from MQTT freshness."""
        self._dbusservice["/Alarms/CommunicationError"] = 2 if stale else 0
        self._dbusservice["/System/StaleData"] = 1 if stale else 0
        if stale != self._comm_alarm_active:
            self._comm_alarm_active = stale
            if stale:
                logger.error("ALARM: No MQTT data received for more than %ss", STALE_TIMEOUT)
            else:
                logger.info("MQTT data fresh again, CommunicationError cleared")

    def update(self):
        """Update D-Bus values from MQTT data"""
        data = self.mqtt.get_aggregate_data()
        if not data:
            self._dbusservice["/Connected"] = 0
            self._set_communication_error(True)
            return

        self._dbusservice["/Connected"] = 1

        # BMS communication staleness (any subscribed MQTT topic)
        self._set_communication_error((time() - self.mqtt.last_message_time) > STALE_TIMEOUT)

        # DC measurements
        self._dbusservice[PATH_DC_VOLTAGE] = round(data["voltage"], 2)
        self._dbusservice[PATH_DC_CURRENT] = round(data["current"], 2)
        self._dbusservice[PATH_DC_POWER] = round(data["power"], 0)
        self._dbusservice["/Dc/0/Temperature"] = round(data["temperature"], 1)

        # State of charge
        self._dbusservice["/Soc"] = round(data["soc"], 1)
        self._dbusservice["/Capacity"] = round(data["capacity"], 1)
        if data.get("capacity_full") and data["capacity_full"] > 0:
            self._dbusservice["/InstalledCapacity"] = round(data["capacity_full"], 0)

        self._update_time_to_go(data)

        # Cell voltages with IDs
        self._dbusservice["/System/NrOfCellsPerBattery"] = self.mqtt.cells_per_bms
        self._update_cell_voltages(data)

        # Per-battery temperatures plus min/max with IDs
        self._update_temperature_paths(data)

        # Modules status
        valid_count = sum(1 for b in self.mqtt.batteries.values() if b.is_valid())
        online_count = data.get("modules_online", valid_count)
        offline_count = data.get("modules_offline", 0)
        blocking_charge = data.get("modules_blocking_charge", 0)
        blocking_discharge = data.get("modules_blocking_discharge", 0)

        self._dbusservice["/System/NrOfModulesOnline"] = online_count
        self._dbusservice["/System/NrOfModulesOffline"] = (
            self.mqtt.battery_count - valid_count + offline_count
        )
        self._dbusservice["/System/NrOfModulesBlockingCharge"] = blocking_charge
        self._dbusservice["/System/NrOfModulesBlockingDischarge"] = blocking_discharge

        # History
        self._dbusservice["/History/ChargeCycles"] = data["cycles"]

        # Charge/discharge control
        self._dbusservice["/Io/AllowToCharge"] = 1 if data["allow_charge"] else 0
        self._dbusservice["/Io/AllowToDischarge"] = 1 if data["allow_discharge"] else 0

        # Update alarms based on data
        self._update_alarms(data)

        # DVCC: Dynamic Voltage and Current Control
        # Calculate and publish CCL/DCL/CVL for Victron to use
        self._update_dvcc(data)

    def _update_time_to_go(self, data: dict[str, Any]) -> None:
        """Compute and publish estimated time-to-go (in seconds)."""
        current = data["current"]
        capacity = data["capacity"]
        capacity_full = data.get("capacity_full", 0)

        # Sanity check: capacity_full should be reasonable (< 10x remaining capacity)
        if capacity_full > capacity * 10 or capacity_full < capacity:
            capacity_full = capacity * 100 / max(10, data["soc"])

        if current < -0.5 and capacity > 0:
            # Discharging: time = remaining capacity / discharge current
            hours = capacity / abs(current)
            # Cap at 7 days max
            self._dbusservice[PATH_TIME_TO_GO] = min(int(hours * 3600), 7 * 24 * 3600)
        elif current > 0.5 and capacity_full > capacity:
            # Charging: time = (full - remaining) / charge current
            hours = (capacity_full - capacity) / current
            # Cap at 7 days max
            self._dbusservice[PATH_TIME_TO_GO] = min(int(hours * 3600), 7 * 24 * 3600)
        else:
            # Idle or very low current - no meaningful time-to-go
            self._dbusservice[PATH_TIME_TO_GO] = None

    def _update_cell_voltages(self, data: dict[str, Any]) -> None:
        """Publish min/max cell info and per-cell voltages for GUI v2."""
        if data["min_cell"] is not None:
            self._dbusservice["/System/MinCellVoltage"] = round(data["min_cell"], 3)
            self._dbusservice["/System/MinVoltageCellId"] = data.get("min_cell_id", 1)
        if data["max_cell"] is not None:
            self._dbusservice["/System/MaxCellVoltage"] = round(data["max_cell"], 3)
            self._dbusservice["/System/MaxVoltageCellId"] = data.get("max_cell_id", 1)
        if data["min_cell"] and data["max_cell"]:
            self._dbusservice["/Voltages/Sum"] = round(data["voltage"], 2)
            self._dbusservice["/Voltages/Diff"] = round(data["max_cell"] - data["min_cell"], 3)

        # Update individual cell voltages for GUI v2
        all_cells = data.get("all_cells", [])
        total_cells = self.mqtt.battery_count * self.mqtt.cells_per_bms

        # Update /System/NrOfCells
        self._dbusservice["/System/NrOfCells"] = total_cells

        for cell_id, voltage in all_cells:
            # cell_id is 1-indexed from get_aggregate_data
            # Convert to 0-indexed for /Cell/{i}/Voltage
            cell_idx_0 = cell_id - 1

            if 0 <= cell_idx_0 < total_cells:
                voltage_rounded = round(voltage, 3)
                try:
                    # GUI v2 standard path (0-indexed)
                    self._dbusservice[f"/Cell/{cell_idx_0}/Voltage"] = voltage_rounded
                    self._dbusservice[f"/Cell/{cell_idx_0}/Balance"] = 0

                    # Legacy path for backward compatibility (1-indexed)
                    self._dbusservice[f"/Voltages/Cell{cell_id}"] = voltage_rounded
                    self._dbusservice[f"/Balances/Cell{cell_id}"] = 0
                except (KeyError, TypeError, ValueError):
                    logger.debug("Failed to write cell voltage to D-Bus for cell %d", cell_id)

    def _update_temperature_paths(self, data: dict[str, Any]) -> None:
        """Publish per-battery temperatures plus min/max with IDs."""
        temps = data.get("temperatures", {})
        for bms_id, temp in temps.items():
            if temp is not None:
                try:
                    self._dbusservice[f"/System/Temperature{bms_id}"] = round(temp, 1)
                except (KeyError, TypeError, ValueError):
                    logger.debug("Failed to write temperature to D-Bus for BMS %d", bms_id)

        # Temperature with IDs
        if data.get("min_temp") is not None:
            self._dbusservice["/System/MinCellTemperature"] = round(data["min_temp"], 1)
            self._dbusservice["/System/MinTemperatureCellId"] = data.get("min_temp_id", 1)
        if data.get("max_temp") is not None:
            self._dbusservice["/System/MaxCellTemperature"] = round(data["max_temp"], 1)
            self._dbusservice["/System/MaxTemperatureCellId"] = data.get("max_temp_id", 1)

    def _update_soc_alarm(self, data: dict[str, Any]):
        """Update low state-of-charge alarm."""
        soc = data.get("soc", 100)
        if soc <= self.config.battery.alarm_low_soc_critical:
            self._dbusservice[ALARM_PATH_LOW_SOC] = 2
            logger.warning("ALARM: Critical Low SoC (%s%%)", soc)
        elif soc <= self.config.battery.alarm_low_soc:
            self._dbusservice[ALARM_PATH_LOW_SOC] = 1
            logger.warning("WARNING: Low SoC (%s%%)", soc)
        else:
            self._dbusservice[ALARM_PATH_LOW_SOC] = 0

    def _update_low_cell_voltage_alarm(self, min_cell, min_cell_id):
        """Update low cell voltage alarm."""
        if min_cell is None:
            return
        if min_cell <= self.config.battery.alarm_low_cell_critical:
            self._dbusservice[ALARM_PATH_LOW_CELL_VOLTAGE] = 2
            logger.warning(
                "ALARM: Critical Low Cell Voltage (%.3fV, Cell %s)",
                min_cell,
                min_cell_id,
            )
        elif min_cell <= self.config.battery.alarm_low_cell_voltage:
            self._dbusservice[ALARM_PATH_LOW_CELL_VOLTAGE] = 1
            logger.warning(
                "WARNING: Low Cell Voltage (%.3fV, Cell %s)",
                min_cell,
                min_cell_id,
            )
        else:
            self._dbusservice[ALARM_PATH_LOW_CELL_VOLTAGE] = 0

    def _update_high_cell_voltage_alarm(self, max_cell, max_cell_id):
        """Update high cell voltage alarm."""
        if max_cell is None:
            return
        if max_cell >= self.config.battery.alarm_high_cell_critical:
            self._dbusservice[ALARM_PATH_HIGH_CELL_VOLTAGE] = 2
            logger.warning(
                "ALARM: Critical High Cell Voltage (%.3fV, Cell %s)",
                max_cell,
                max_cell_id,
            )
        elif max_cell >= self.config.battery.alarm_high_cell_voltage:
            self._dbusservice[ALARM_PATH_HIGH_CELL_VOLTAGE] = 1
            logger.warning(
                "WARNING: High Cell Voltage (%.3fV, Cell %s)",
                max_cell,
                max_cell_id,
            )
        else:
            self._dbusservice[ALARM_PATH_HIGH_CELL_VOLTAGE] = 0

    def _update_cell_imbalance_alarm(self, min_cell, max_cell):
        """Update cell imbalance alarm."""
        if min_cell is None or max_cell is None:
            return
        diff = max_cell - min_cell
        if diff >= self.config.battery.alarm_cell_imbalance * 2:
            self._dbusservice[ALARM_PATH_CELL_IMBALANCE] = 2
            logger.warning("ALARM: High Cell Imbalance (%.3fV)", diff)
        elif diff >= self.config.battery.alarm_cell_imbalance:
            self._dbusservice[ALARM_PATH_CELL_IMBALANCE] = 1
        else:
            self._dbusservice[ALARM_PATH_CELL_IMBALANCE] = 0

    def _update_temperature_alarms(self, data: dict[str, Any]):
        """Update high/low temperature alarms."""
        max_temp = data.get("max_temp", 25)
        min_temp = data.get("min_temp", 25)

        if max_temp >= self.config.battery.alarm_high_temp_critical:
            self._dbusservice[ALARM_PATH_HIGH_TEMPERATURE] = 2
            logger.warning("ALARM: Critical High Temperature (%s°C)", max_temp)
        elif max_temp >= self.config.battery.alarm_high_temp:
            self._dbusservice[ALARM_PATH_HIGH_TEMPERATURE] = 1
            logger.warning("WARNING: High Temperature (%s°C)", max_temp)
        else:
            self._dbusservice[ALARM_PATH_HIGH_TEMPERATURE] = 0

        if min_temp <= self.config.battery.alarm_low_temp_critical:
            self._dbusservice[ALARM_PATH_LOW_TEMPERATURE] = 2
            logger.warning("ALARM: Critical Low Temperature (%s°C)", min_temp)
        elif min_temp <= self.config.battery.alarm_low_temp:
            self._dbusservice[ALARM_PATH_LOW_TEMPERATURE] = 1
            logger.warning("WARNING: Low Temperature (%s°C)", min_temp)
        else:
            self._dbusservice[ALARM_PATH_LOW_TEMPERATURE] = 0

    def _update_internal_failure_alarm(self, data: dict[str, Any]):
        """Update BMS protection / internal failure alarm.

        Triggered when discharging is blocked but should be discharging,
        indicating the BMS has entered protection mode.
        """
        modules_blocking = data.get("modules_blocking_discharge", 0)
        modules_offline = data.get("modules_offline", 0)

        if modules_offline > 0:
            # Some modules are offline - critical alarm
            self._dbusservice[ALARM_PATH_INTERNAL_FAILURE] = 2
            logger.warning("ALARM: %d module(s) OFFLINE!", modules_offline)
        elif modules_blocking > 0:
            # Some modules are blocking discharge - warning
            self._dbusservice[ALARM_PATH_INTERNAL_FAILURE] = 1
            logger.warning(
                "WARNING: %d module(s) blocking discharge (BMS protection active)",
                modules_blocking,
            )
        else:
            self._dbusservice[ALARM_PATH_INTERNAL_FAILURE] = 0

    def _update_voltage_alarms(self, data: dict[str, Any]):
        """Update low/high aggregate voltage alarms."""
        voltage = data.get("voltage", 0)
        if voltage <= 0:
            return

        cell_count = data.get("cell_count", 16)
        expected_nominal = cell_count * 3.2  # LiFePO4 nominal
        expected_min = cell_count * 2.8
        expected_max = cell_count * 3.65

        if voltage <= expected_min:
            self._dbusservice[ALARM_PATH_LOW_VOLTAGE] = 2
        elif voltage <= expected_nominal * 0.9:
            self._dbusservice[ALARM_PATH_LOW_VOLTAGE] = 1
        else:
            self._dbusservice[ALARM_PATH_LOW_VOLTAGE] = 0

        if voltage >= expected_max:
            self._dbusservice[ALARM_PATH_HIGH_VOLTAGE] = 2
        elif voltage >= expected_nominal * 1.1:
            self._dbusservice[ALARM_PATH_HIGH_VOLTAGE] = 1
        else:
            self._dbusservice[ALARM_PATH_HIGH_VOLTAGE] = 0

    def _update_alarms(self, data: dict[str, Any]):
        """Update alarm states based on battery data.

        Alarm values: 0 = OK, 1 = Warning, 2 = Alarm/Critical
        """
        min_cell = data.get("min_cell")
        max_cell = data.get("max_cell")

        self._update_soc_alarm(data)
        self._update_low_cell_voltage_alarm(min_cell, data.get("min_cell_id", "?"))
        self._update_high_cell_voltage_alarm(max_cell, data.get("max_cell_id", "?"))
        self._update_cell_imbalance_alarm(min_cell, max_cell)
        self._update_temperature_alarms(data)
        self._update_internal_failure_alarm(data)
        self._update_voltage_alarms(data)

    def _update_dvcc(self, data: dict[str, Any]):
        """
        Update DVCC (Dynamic Voltage and Current Control) values.

        This is the critical function that tells Victron how much current
        the battery can accept. When a cell voltage is high, we reduce CCL
        to give balancers time to work and prevent BMS emergency cutoff.

        Victron MPPT/Inverter will respect these limits when DVCC is enabled
        in the GX device settings.
        """
        # Calculate DVCC parameters
        dvcc = self.dvcc.calculate(data)

        ccl = dvcc["ccl"]
        dcl = dvcc["dcl"]
        cvl = dvcc["cvl"]
        ccl_reason = dvcc["ccl_reason"]
        # dcl_reason is intentionally not used
        _ = dvcc["dcl_reason"]
        max_cell = dvcc.get("max_cell_voltage")
        max_cell_id = dvcc.get("max_cell_id")
        cell_delta = dvcc.get("cell_delta")

        # Update D-Bus values for Victron DVCC
        self._dbusservice["/Info/MaxChargeCurrent"] = ccl
        self._dbusservice["/Info/MaxDischargeCurrent"] = dcl
        self._dbusservice["/Info/MaxChargeVoltage"] = cvl

        # Update max cell voltage for reference
        if max_cell is not None:
            self._dbusservice["/Info/MaxChargeCellVoltage"] = round(max_cell, 3)

        # Log DVCC status periodically or on significant changes
        now = time()
        should_log = False
        is_limiting = ccl < DVCC_MAX_CHARGE_CURRENT * 0.9

        # Log when heavily limited; otherwise periodic (interval, or 4x interval when not limiting)
        if ccl < DVCC_MAX_CHARGE_CURRENT * 0.5:
            should_log = True
        else:
            log_interval = self.dvcc_log_interval if is_limiting else self.dvcc_log_interval * 4
            should_log = (now - self.last_dvcc_log) > log_interval

        if should_log:
            self.last_dvcc_log = self._log_dvcc_status(
                now,
                ccl,
                ccl_reason,
                dcl,
                cvl,
                max_cell_id,
                max_cell,
                cell_delta,
            )

        # If CCL is critically low, log warning with cell info
        if ccl <= DVCC_MIN_CHARGE_CURRENT and ccl_reason not in (
            "normal",
            "soc_ok",
            "temp_ok",
            "balanced",
        ):
            cell_info = (
                f"Cell {max_cell_id} at {max_cell:.3f}V"
                if max_cell is not None and max_cell_id is not None
                else ccl_reason
            )
            logger.warning("DVCC: Charge current limited to %.1fA! Reason: %s", ccl, cell_info)

    def _log_dvcc_status(
        self,
        now: float,
        ccl: float,
        ccl_reason: str,
        dcl: float,
        cvl: float,
        max_cell_id: int | None,
        max_cell: float | None,
        cell_delta: float | None,
    ) -> float:
        """Log DVCC status and return updated last_dvcc_log."""
        delta_str = f", Δ={cell_delta:.3f}V" if cell_delta is not None else ""
        cell_info = (
            f"Cell {max_cell_id}={max_cell:.3f}V"
            if max_cell is not None and max_cell_id is not None
            else ""
        )

        if ccl < DVCC_MAX_CHARGE_CURRENT * 0.5:
            logger.info(
                "DVCC limiting current to %.1fA because of %s%s",
                ccl,
                cell_info,
                delta_str,
            )
        else:
            logger.info(
                "DVCC: CCL=%.1fA (%s), DCL=%.1fA, CVL=%.1fV, %s%s",
                ccl,
                ccl_reason,
                dcl,
                cvl,
                cell_info,
                delta_str,
            )
        return now


# =============================================================================
# MAIN
# =============================================================================


def main():
    """Main entry point for MQTT battery D-Bus service."""
    config, _args = load_config()

    logger.info("=== dbus-mqtt-battery v%s ===", VERSION)
    logger.info("MQTT Broker: %s:%s", config.mqtt.broker, config.mqtt.port)
    logger.info("Topic prefix: %s", config.mqtt.topic_prefix)
    logger.info(
        "Number of batteries: %s, MQTT BMS index starts at: %s",
        config.battery.count,
        config.battery.bms_first,
    )
    logger.info("D-Bus service: com.victronenergy.battery.%s", config.dbus.service_suffix)

    # Setup D-Bus main loop
    mainloop = setup_main_loop()

    # Variables for cleanup
    mqtt_client = None

    # Register signal handlers
    register_signal_handlers(mainloop)

    # Create MQTT client
    mqtt_client = MqttBatteryClient(
        config.mqtt.broker,
        config.mqtt.port,
        config.battery.count,
        config.mqtt.topic_prefix,
        config.battery.capacity,
        config.battery.bms_first,
        config.battery.cells_per_bms,
    )
    if not mqtt_client.connect():
        logger.warning("Failed to connect to MQTT broker")
        sys.exit(1)

    # Wait for initial data
    logger.info("Waiting for MQTT data...")

    sleep(5)

    # Create D-Bus service
    dbus_service = DbusAggregateService(
        mqtt_client,
        config.dbus.instance,
        config.dbus.service_suffix,
        config.dbus.product_name,
        config=config,
    )

    # Heartbeat file for watchdog
    heartbeat_file = "/run/dbus-mqtt-battery.alive"

    # Create poll function with GC and heartbeat
    poll_fn = create_poll_function(dbus_service, heartbeat_file)

    # Start polling and run main loop
    run_main_loop(mainloop, POLL_INTERVAL_MS, poll_fn)


if __name__ == "__main__":
    main()
