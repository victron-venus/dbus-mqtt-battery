#!/usr/bin/python3
# -*- coding: utf-8 -*-
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

import sys
import os
import argparse
import logging
from time import time, sleep
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

# First-party imports (local modules)
from dvcc import (
    DvccController,
    DVCC_MAX_CHARGE_CURRENT,
    DVCC_MAX_DISCHARGE_CURRENT,
    DVCC_MIN_CHARGE_CURRENT,
    DVCC_CELL_MAX_VOLTAGE,
)

# Import from package (replaces duplicated BatteryData and MqttBatteryClient)
from dbus_mqtt_battery import (
    MqttBatteryClient,
    VERSION,
    POLL_INTERVAL_MS,
    DVCC_CELLS_PER_BMS,
    get_bus,
    setup_main_loop,
    register_signal_handlers,
    create_poll_function,
    run_main_loop,
    setup_dbus_paths_common,
    setup_dbus_paths_dc,
    setup_dbus_paths_alarms,
)

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("MqttBattery")

# =============================================================================
# CONFIGURATION
# =============================================================================

DEFAULT_MQTT_BROKER = "localhost"
DEFAULT_MQTT_PORT = 1883

# Alarm thresholds (adjust for your battery type)
ALARM_LOW_SOC = 10  # % - Low state of charge warning
ALARM_LOW_SOC_CRITICAL = 5  # % - Critical low SoC
ALARM_LOW_CELL_VOLTAGE = 2.9  # V - Low cell voltage warning
ALARM_LOW_CELL_CRITICAL = 2.7  # V - Critical low cell voltage
ALARM_HIGH_CELL_VOLTAGE = 3.55  # V - High cell voltage warning
ALARM_HIGH_CELL_CRITICAL = 3.65  # V - Critical high cell voltage
ALARM_CELL_IMBALANCE = 0.1  # V - Cell imbalance warning
ALARM_HIGH_TEMP = 45  # °C - High temperature warning
ALARM_HIGH_TEMP_CRITICAL = 55  # °C - Critical high temperature
ALARM_LOW_TEMP = 0  # °C - Low temperature warning
ALARM_LOW_TEMP_CRITICAL = -10  # °C - Critical low temperature

# Alias for backward compatibility
CELLS_PER_BMS = DVCC_CELLS_PER_BMS


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
    ):
        self.mqtt = mqtt_client
        self.device_instance = device_instance
        self.product_name = product_name

        # Initialize DVCC controller for dynamic current limiting
        total_cells = mqtt_client.battery_count * mqtt_client.cells_per_bms
        self.dvcc = DvccController(total_cells, mqtt_client.battery_count)
        self.dvcc_log_interval = 30  # Log DVCC status every N seconds
        self.last_dvcc_log = 0

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
            gettextcallback=lambda a, x: f"{x:.1f}Ah" if x else "",
        )
        self._dbusservice.add_path(
            "/InstalledCapacity",
            None,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.0f}Ah" if x else "",
        )
        self._dbusservice.add_path(
            "/ConsumedAmphours",
            None,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.1f}Ah" if x else "",
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
            gettextcallback=lambda a, x: f"{x:.3f}V" if x else "",
        )
        self._dbusservice.add_path("/System/MinVoltageCellId", None, writeable=True)
        self._dbusservice.add_path(
            "/System/MaxCellVoltage",
            None,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.3f}V" if x else "",
        )
        self._dbusservice.add_path("/System/MaxVoltageCellId", None, writeable=True)
        self._dbusservice.add_path(
            "/Voltages/Sum",
            None,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.2f}V" if x else "",
        )
        self._dbusservice.add_path(
            "/Voltages/Diff",
            None,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.3f}V" if x else "",
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
                gettextcallback=lambda a, x: f"{x:.3f}V" if x else "",
            )
            # Balancing status per cell (for color coding in GUI)
            self._dbusservice.add_path(f"/Cell/{i}/Balance", None, writeable=True)

        # Legacy paths for backward compatibility (dbus-serialbattery format: /Voltages/Cell1..CellN)
        for i in range(1, total_cells + 1):
            self._dbusservice.add_path(
                f"/Voltages/Cell{i}",
                None,
                writeable=True,
                gettextcallback=lambda a, x: f"{x:.3f}V" if x else "",
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
        self._dbusservice.add_path("/TimeToGo", None, writeable=True)

        # Charge/discharge control (DVCC) - default values for 4S LiFePO4
        # CVL = 3.65V × 4 cells × 4 batteries = 58.4V (series config)
        # CCL/DCL = typical limits for 280Ah LiFePO4
        self._dbusservice.add_path(
            "/Info/MaxChargeCurrent",
            100.0,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.1f}A" if x else "",
        )
        self._dbusservice.add_path(
            "/Info/MaxDischargeCurrent",
            120.0,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.1f}A" if x else "",
        )
        self._dbusservice.add_path(
            "/Info/MaxChargeVoltage",
            58.4,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.2f}V" if x else "",
        )
        self._dbusservice.add_path(
            "/Info/MaxChargeCellVoltage",
            3.65,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.3f}V" if x else "",
        )

        # IO
        self._dbusservice.add_path("/Io/AllowToCharge", 1, writeable=True)
        self._dbusservice.add_path("/Io/AllowToDischarge", 1, writeable=True)
        self._dbusservice.add_path("/Io/AllowToBalance", 1, writeable=True)

        # Alarms
        setup_dbus_paths_alarms(self._dbusservice)

        # Reliability: stale data indicator (0=fresh, 1=stale)
        self._dbusservice.add_path("/System/StaleData", 0, writeable=True)

    def update(self):
        """Update D-Bus values from MQTT data"""
        data = self.mqtt.get_aggregate_data()
        if not data:
            self._dbusservice["/Connected"] = 0
            return

        self._dbusservice["/Connected"] = 1

        # DC measurements
        self._dbusservice["/Dc/0/Voltage"] = round(data["voltage"], 2)
        self._dbusservice["/Dc/0/Current"] = round(data["current"], 2)
        self._dbusservice["/Dc/0/Power"] = round(data["power"], 0)
        self._dbusservice["/Dc/0/Temperature"] = round(data["temperature"], 1)

        # State of charge
        self._dbusservice["/Soc"] = round(data["soc"], 1)
        self._dbusservice["/Capacity"] = round(data["capacity"], 1)
        if data.get("capacity_full") and data["capacity_full"] > 0:
            self._dbusservice["/InstalledCapacity"] = round(data["capacity_full"], 0)

        # Time-to-go calculation (in seconds)
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
            time_to_go = min(int(hours * 3600), 7 * 24 * 3600)
            self._dbusservice["/TimeToGo"] = time_to_go
        elif current > 0.5 and capacity_full > capacity:
            # Charging: time = (full - remaining) / charge current
            hours = (capacity_full - capacity) / current
            # Cap at 7 days max
            time_to_go = min(int(hours * 3600), 7 * 24 * 3600)
            self._dbusservice["/TimeToGo"] = time_to_go
        else:
            # Idle or very low current - no meaningful time-to-go
            self._dbusservice["/TimeToGo"] = None

        # Cell voltages with IDs
        total_cells = data["cell_count"]
        self._dbusservice["/System/NrOfCellsPerBattery"] = self.mqtt.cells_per_bms

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

        # Update per-battery temperatures
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

    def _update_alarms(self, data: dict[str, Any]):
        """Update alarm states based on battery data.

        Alarm values: 0 = OK, 1 = Warning, 2 = Alarm/Critical
        """
        # Low SoC alarm
        soc = data.get("soc", 100)
        if soc <= ALARM_LOW_SOC_CRITICAL:
            self._dbusservice["/Alarms/LowSoc"] = 2
            logger.warning("ALARM: Critical Low SoC (%s%%)", soc)
        elif soc <= ALARM_LOW_SOC:
            self._dbusservice["/Alarms/LowSoc"] = 1
            logger.warning("WARNING: Low SoC (%s%%)", soc)
        else:
            self._dbusservice["/Alarms/LowSoc"] = 0

        # Low cell voltage alarm
        min_cell = data.get("min_cell")
        if min_cell is not None:
            if min_cell <= ALARM_LOW_CELL_CRITICAL:
                self._dbusservice["/Alarms/LowCellVoltage"] = 2
                logger.warning(
                    "ALARM: Critical Low Cell Voltage (%.3fV, Cell %s)",
                    min_cell,
                    data.get("min_cell_id", "?"),
                )
            elif min_cell <= ALARM_LOW_CELL_VOLTAGE:
                self._dbusservice["/Alarms/LowCellVoltage"] = 1
                logger.warning(
                    "WARNING: Low Cell Voltage (%.3fV, Cell %s)",
                    min_cell,
                    data.get("min_cell_id", "?"),
                )
            else:
                self._dbusservice["/Alarms/LowCellVoltage"] = 0

        # High cell voltage alarm
        max_cell = data.get("max_cell")
        if max_cell is not None:
            if max_cell >= ALARM_HIGH_CELL_CRITICAL:
                self._dbusservice["/Alarms/HighCellVoltage"] = 2
                logger.warning(
                    "ALARM: Critical High Cell Voltage (%.3fV, Cell %s)",
                    max_cell,
                    data.get("max_cell_id", "?"),
                )
            elif max_cell >= ALARM_HIGH_CELL_VOLTAGE:
                self._dbusservice["/Alarms/HighCellVoltage"] = 1
                logger.warning(
                    "WARNING: High Cell Voltage (%.3fV, Cell %s)",
                    max_cell,
                    data.get("max_cell_id", "?"),
                )
            else:
                self._dbusservice["/Alarms/HighCellVoltage"] = 0

        # Cell imbalance alarm
        if min_cell is not None and max_cell is not None:
            diff = max_cell - min_cell
            if diff >= ALARM_CELL_IMBALANCE * 2:
                self._dbusservice["/Alarms/CellImbalance"] = 2
                logger.warning("ALARM: High Cell Imbalance (%.3fV)", diff)
            elif diff >= ALARM_CELL_IMBALANCE:
                self._dbusservice["/Alarms/CellImbalance"] = 1
            else:
                self._dbusservice["/Alarms/CellImbalance"] = 0

        # Temperature alarms
        max_temp = data.get("max_temp", 25)
        min_temp = data.get("min_temp", 25)

        # High temperature
        if max_temp >= ALARM_HIGH_TEMP_CRITICAL:
            self._dbusservice["/Alarms/HighTemperature"] = 2
            logger.warning("ALARM: Critical High Temperature (%s°C)", max_temp)
        elif max_temp >= ALARM_HIGH_TEMP:
            self._dbusservice["/Alarms/HighTemperature"] = 1
            logger.warning("WARNING: High Temperature (%s°C)", max_temp)
        else:
            self._dbusservice["/Alarms/HighTemperature"] = 0

        # Low temperature
        if min_temp <= ALARM_LOW_TEMP_CRITICAL:
            self._dbusservice["/Alarms/LowTemperature"] = 2
            logger.warning("ALARM: Critical Low Temperature (%s°C)", min_temp)
        elif min_temp <= ALARM_LOW_TEMP:
            self._dbusservice["/Alarms/LowTemperature"] = 1
            logger.warning("WARNING: Low Temperature (%s°C)", min_temp)
        else:
            self._dbusservice["/Alarms/LowTemperature"] = 0

        # BMS protection active (discharging blocked but should be discharging)
        # This indicates BMS has entered protection mode
        modules_blocking = data.get("modules_blocking_discharge", 0)
        modules_offline = data.get("modules_offline", 0)

        if modules_offline > 0:
            # Some modules are offline - critical alarm
            self._dbusservice["/Alarms/InternalFailure"] = 2
            logger.warning("ALARM: %d module(s) OFFLINE!", modules_offline)
        elif modules_blocking > 0:
            # Some modules are blocking discharge - warning
            self._dbusservice["/Alarms/InternalFailure"] = 1
            logger.warning(
                "WARNING: %d module(s) blocking discharge (BMS protection active)",
                modules_blocking,
            )
        else:
            self._dbusservice["/Alarms/InternalFailure"] = 0

        # Low/High voltage (aggregate)
        voltage = data.get("voltage", 0)
        cell_count = data.get("cell_count", 16)
        expected_nominal = cell_count * 3.2  # LiFePO4 nominal
        expected_min = cell_count * 2.8
        expected_max = cell_count * 3.65

        if voltage > 0:
            if voltage <= expected_min:
                self._dbusservice["/Alarms/LowVoltage"] = 2
            elif voltage <= expected_nominal * 0.9:
                self._dbusservice["/Alarms/LowVoltage"] = 1
            else:
                self._dbusservice["/Alarms/LowVoltage"] = 0

            if voltage >= expected_max:
                self._dbusservice["/Alarms/HighVoltage"] = 2
            elif voltage >= expected_nominal * 1.1:
                self._dbusservice["/Alarms/HighVoltage"] = 1
            else:
                self._dbusservice["/Alarms/HighVoltage"] = 0

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
        dcl_reason = dvcc["dcl_reason"]
        max_cell = dvcc.get("max_cell_voltage")
        max_cell_id = dvcc.get("max_cell_id")
        min_cell = dvcc.get("min_cell_voltage")
        min_cell_id = dvcc.get("min_cell_id")
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

        # Log if CCL is significantly limited
        if is_limiting:
            if ccl < DVCC_MAX_CHARGE_CURRENT * 0.5:
                should_log = True  # Log when heavily limited
            elif (now - self.last_dvcc_log) > self.dvcc_log_interval:
                should_log = True
        elif (now - self.last_dvcc_log) > self.dvcc_log_interval * 4:
            should_log = True  # Periodic status update

        if should_log:
            self.last_dvcc_log = now
            delta_str = f", Δ={cell_delta:.3f}V" if cell_delta is not None else ""
            cell_info = (
                f"Cell {max_cell_id}={max_cell:.3f}V"
                if max_cell is not None and max_cell_id is not None
                else ""
            )

            if is_limiting and max_cell_id is not None:
                # Clear message when limiting due to cell voltage
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


# =============================================================================
# MAIN
# =============================================================================


def main():
    """Main entry point for MQTT battery D-Bus service."""
    parser = argparse.ArgumentParser(description="MQTT to D-Bus Battery Bridge for Victron")
    parser.add_argument("--broker", default=DEFAULT_MQTT_BROKER, help="MQTT broker address")
    parser.add_argument("--port", type=int, default=DEFAULT_MQTT_PORT, help="MQTT broker port")
    parser.add_argument("--batteries", type=int, default=4, help="Number of batteries")
    parser.add_argument("--instance", type=int, default=512, help="D-Bus device instance")
    parser.add_argument(
        "--topic-prefix", default="battery", help="MQTT topic prefix (default: battery)"
    )
    parser.add_argument(
        "--service-suffix",
        default="mqtt_chain",
        help="D-Bus service suffix (default: mqtt_chain)",
    )
    parser.add_argument("--product-name", default="JBD Battery Chain", help="Product name in GUI")
    parser.add_argument(
        "--capacity",
        type=float,
        default=280,
        help="Installed capacity in Ah (for series-connected batteries)",
    )
    parser.add_argument(
        "--cells-per-bms",
        type=int,
        default=4,
        help="Number of cells per BMS module (default: 4 for 12V LiFePO4)",
    )
    parser.add_argument(
        "--bms-first",
        type=int,
        default=1,
        help="First MQTT BMS index for this chain (chain1: 1, chain2 with bms3+bms4: 3)",
    )
    args = parser.parse_args()

    logger.info("=== dbus-mqtt-battery v%s ===", VERSION)
    logger.info("MQTT Broker: %s:%s", args.broker, args.port)
    logger.info("Topic prefix: %s", args.topic_prefix)
    logger.info(
        "Number of batteries: %s, MQTT BMS index starts at: %s",
        args.batteries,
        args.bms_first,
    )
    logger.info("D-Bus service: com.victronenergy.battery.%s", args.service_suffix)

    # Setup D-Bus main loop
    mainloop = setup_main_loop()

    # Variables for cleanup
    mqtt_client = None

    # Register signal handlers
    register_signal_handlers(mainloop)

    # Create MQTT client
    mqtt_client = MqttBatteryClient(
        args.broker,
        args.port,
        args.batteries,
        args.topic_prefix,
        args.capacity,
        args.bms_first,
        args.cells_per_bms,
    )
    if not mqtt_client.connect():
        logger.error("Failed to connect to MQTT broker")
        sys.exit(1)

    # Wait for initial data
    logger.info("Waiting for MQTT data...")

    sleep(5)

    # Create D-Bus service
    dbus_service = DbusAggregateService(
        mqtt_client, args.instance, args.service_suffix, args.product_name
    )

    # Heartbeat file for watchdog
    heartbeat_file = "/run/dbus-mqtt-battery.alive"

    # Create poll function with GC and heartbeat
    poll_fn = create_poll_function(dbus_service, heartbeat_file)

    # Start polling and run main loop
    run_main_loop(mainloop, POLL_INTERVAL_MS, poll_fn)


if __name__ == "__main__":
    main()
