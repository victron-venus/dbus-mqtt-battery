#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
dbus-mqtt-battery - MQTT to D-Bus Bridge for JBD BMS via ESP32
==============================================================

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

import sys
import os
import argparse
import logging
import re
import signal
import gc
from typing import Dict, Any, Optional, List
from time import sleep, time
from threading import Lock

# Add Victron library path
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 
    "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python"))

from dbus.mainloop.glib import DBusGMainLoop
import dbus

if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject

try:
    import paho.mqtt.client as mqtt
    # Support for paho-mqtt v2.0+
    try:
        from paho.mqtt.enums import CallbackAPIVersion
        PAHO_V2 = True
    except ImportError:
        PAHO_V2 = False
except ImportError:
    print("ERROR: paho-mqtt not installed. Run: pip install paho-mqtt")
    sys.exit(1)

from vedbus import VeDbusService

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger("MqttBattery")

# =============================================================================
# CONFIGURATION
# =============================================================================

VERSION = "2.6.0"
DEFAULT_MQTT_BROKER = "localhost"
DEFAULT_MQTT_PORT = 1883
POLL_INTERVAL_MS = 2000
STALE_TIMEOUT = 60  # seconds before data considered stale

# Alarm thresholds (adjust for your battery type)
ALARM_LOW_SOC = 10              # % - Low state of charge warning
ALARM_LOW_SOC_CRITICAL = 5      # % - Critical low SoC
ALARM_LOW_CELL_VOLTAGE = 2.9    # V - Low cell voltage warning
ALARM_LOW_CELL_CRITICAL = 2.7   # V - Critical low cell voltage
ALARM_HIGH_CELL_VOLTAGE = 3.55  # V - High cell voltage warning  
ALARM_HIGH_CELL_CRITICAL = 3.65 # V - Critical high cell voltage
ALARM_CELL_IMBALANCE = 0.1      # V - Cell imbalance warning
ALARM_HIGH_TEMP = 45            # °C - High temperature warning
ALARM_HIGH_TEMP_CRITICAL = 55   # °C - Critical high temperature
ALARM_LOW_TEMP = 0              # °C - Low temperature warning
ALARM_LOW_TEMP_CRITICAL = -10   # °C - Critical low temperature

# =============================================================================
# DVCC CONFIGURATION (Dynamic Voltage and Current Control)
# =============================================================================
# These settings allow Victron to protect cells by dynamically limiting
# charge current when any cell approaches dangerous voltage levels.

# Cell voltage thresholds for CCL (Charge Current Limit) calculation
DVCC_CELL_FULL_CURRENT = 3.40      # V - Below this: 100% charge current
DVCC_CELL_START_LIMIT = 3.45       # V - Start reducing current
DVCC_CELL_BALANCE_VOLTAGE = 3.50   # V - Aggressive reduction, balancers working
DVCC_CELL_NEAR_FULL = 3.55         # V - Minimal current (tail charge)
DVCC_CELL_CUTOFF = 3.60            # V - Stop charging completely

# Maximum currents (adjust for your battery system)
DVCC_MAX_CHARGE_CURRENT = 100.0    # A - Maximum charge current at normal conditions
DVCC_MAX_DISCHARGE_CURRENT = 120.0 # A - Maximum discharge current
DVCC_MIN_CHARGE_CURRENT = 2.0      # A - Minimum tail charge current (for balancing)

# CVL settings (Charge Voltage Limit)
DVCC_CELL_MAX_VOLTAGE = 3.65       # V - Maximum cell voltage (for CVL calculation)
DVCC_CELLS_PER_BMS = 4             # Cells per BMS module

# Cell imbalance protection
DVCC_IMBALANCE_START_LIMIT = 0.05  # V - Start reducing current if delta > this
DVCC_IMBALANCE_AGGRESSIVE = 0.10   # V - Aggressive reduction
DVCC_IMBALANCE_CRITICAL = 0.20     # V - Minimal current

# Temperature-based current limiting
DVCC_TEMP_FULL_CURRENT_MIN = 10    # °C - Full current above this temp
DVCC_TEMP_FULL_CURRENT_MAX = 40    # °C - Full current below this temp
DVCC_TEMP_STOP_CHARGE = 0          # °C - Stop charging below this temp
DVCC_TEMP_STOP_CHARGE_HIGH = 50    # °C - Stop charging above this temp

# SoC-based reduction (optional, for extending battery life)
DVCC_SOC_REDUCE_START = 95         # % - Start reducing current above this SoC
DVCC_SOC_REDUCE_FACTOR = 0.5       # Factor at 100% SoC (0.5 = 50% of max current)

# =============================================================================
# DVCC CONTROLLER
# =============================================================================

class DvccController:
    """
    Dynamic Voltage and Current Control for cell protection.
    
    Calculates CCL (Charge Current Limit) and DCL (Discharge Current Limit)
    based on:
    - Highest cell voltage (most critical for charge protection)
    - Cell voltage imbalance (delta between min and max cells)
    - Temperature limits
    - SoC (optional, for battery longevity)
    
    The goal is to protect cells BEFORE BMS triggers emergency cutoff,
    allowing balancers time to work and preventing system shutdowns.
    """
    
    def __init__(self, cell_count: int, bms_count: int):
        self.cell_count = cell_count
        self.bms_count = bms_count
        self.last_ccl = DVCC_MAX_CHARGE_CURRENT
        self.last_dcl = DVCC_MAX_DISCHARGE_CURRENT
        self.last_cvl = DVCC_CELL_MAX_VOLTAGE * cell_count
        
        # Rate limiting for smooth transitions
        self.ccl_change_rate = 10.0  # Max A/s change for CCL (smoothing)
        self.last_update_time = time()
    
    def calculate_ccl_from_cell_voltage(self, max_cell_voltage: float) -> tuple[float, str]:
        """
        Calculate CCL based on highest cell voltage.
        Returns (current_limit, reason_string).
        
        Uses linear interpolation between voltage thresholds for smooth control.
        """
        if max_cell_voltage is None:
            return DVCC_MAX_CHARGE_CURRENT, "no_cell_data"
        
        v = max_cell_voltage
        
        # Below threshold - full current
        if v <= DVCC_CELL_FULL_CURRENT:
            return DVCC_MAX_CHARGE_CURRENT, "normal"
        
        # Cell cutoff - stop charging
        if v >= DVCC_CELL_CUTOFF:
            return 0.0, f"cell_overvoltage_{v:.3f}V"
        
        # Near full - minimal current for balancing
        if v >= DVCC_CELL_NEAR_FULL:
            # Linear reduction from MIN_CHARGE_CURRENT to 0 between NEAR_FULL and CUTOFF
            factor = 1.0 - (v - DVCC_CELL_NEAR_FULL) / (DVCC_CELL_CUTOFF - DVCC_CELL_NEAR_FULL)
            ccl = DVCC_MIN_CHARGE_CURRENT * factor
            return max(0.0, ccl), f"tail_charge_{v:.3f}V"
        
        # Balance voltage - aggressive reduction
        if v >= DVCC_CELL_BALANCE_VOLTAGE:
            # Linear reduction from ~20% to MIN_CHARGE_CURRENT
            factor = 1.0 - (v - DVCC_CELL_BALANCE_VOLTAGE) / (DVCC_CELL_NEAR_FULL - DVCC_CELL_BALANCE_VOLTAGE)
            ccl = DVCC_MIN_CHARGE_CURRENT + (DVCC_MAX_CHARGE_CURRENT * 0.20 - DVCC_MIN_CHARGE_CURRENT) * factor
            return ccl, f"balancing_{v:.3f}V"
        
        # Start limiting - gradual reduction
        if v >= DVCC_CELL_START_LIMIT:
            # Linear reduction from 100% to 20%
            factor = 1.0 - (v - DVCC_CELL_START_LIMIT) / (DVCC_CELL_BALANCE_VOLTAGE - DVCC_CELL_START_LIMIT)
            ccl = DVCC_MAX_CHARGE_CURRENT * (0.20 + 0.80 * factor)
            return ccl, f"reducing_{v:.3f}V"
        
        # Between FULL_CURRENT and START_LIMIT - full current
        return DVCC_MAX_CHARGE_CURRENT, "normal"
    
    def calculate_ccl_from_imbalance(self, cell_delta: float) -> tuple[float, str]:
        """
        Calculate CCL reduction based on cell voltage imbalance.
        Returns (current_limit, reason_string).
        
        High imbalance indicates one cell is "running away" and needs
        time for balancers to catch up.
        """
        if cell_delta is None or cell_delta < 0:
            return DVCC_MAX_CHARGE_CURRENT, "no_delta"
        
        # Normal imbalance
        if cell_delta <= DVCC_IMBALANCE_START_LIMIT:
            return DVCC_MAX_CHARGE_CURRENT, "balanced"
        
        # Critical imbalance
        if cell_delta >= DVCC_IMBALANCE_CRITICAL:
            return DVCC_MIN_CHARGE_CURRENT, f"critical_imbalance_{cell_delta:.3f}V"
        
        # Aggressive zone
        if cell_delta >= DVCC_IMBALANCE_AGGRESSIVE:
            factor = 1.0 - (cell_delta - DVCC_IMBALANCE_AGGRESSIVE) / (DVCC_IMBALANCE_CRITICAL - DVCC_IMBALANCE_AGGRESSIVE)
            ccl = DVCC_MIN_CHARGE_CURRENT + (DVCC_MAX_CHARGE_CURRENT * 0.30 - DVCC_MIN_CHARGE_CURRENT) * factor
            return ccl, f"imbalance_{cell_delta:.3f}V"
        
        # Start limiting zone
        factor = 1.0 - (cell_delta - DVCC_IMBALANCE_START_LIMIT) / (DVCC_IMBALANCE_AGGRESSIVE - DVCC_IMBALANCE_START_LIMIT)
        ccl = DVCC_MAX_CHARGE_CURRENT * (0.30 + 0.70 * factor)
        return ccl, f"slight_imbalance_{cell_delta:.3f}V"
    
    def calculate_ccl_from_temperature(self, min_temp: float, max_temp: float) -> tuple[float, str]:
        """
        Calculate CCL based on temperature limits.
        Returns (current_limit, reason_string).
        
        LiFePO4 should not be charged below 0°C (lithium plating risk)
        and should have reduced current at high temperatures.
        """
        if min_temp is None:
            min_temp = 25.0
        if max_temp is None:
            max_temp = 25.0
        
        # Too cold - stop charging
        if min_temp <= DVCC_TEMP_STOP_CHARGE:
            return 0.0, f"too_cold_{min_temp:.1f}C"
        
        # Too hot - stop charging
        if max_temp >= DVCC_TEMP_STOP_CHARGE_HIGH:
            return 0.0, f"too_hot_{max_temp:.1f}C"
        
        # Cold but chargeable - reduce current
        if min_temp < DVCC_TEMP_FULL_CURRENT_MIN:
            factor = (min_temp - DVCC_TEMP_STOP_CHARGE) / (DVCC_TEMP_FULL_CURRENT_MIN - DVCC_TEMP_STOP_CHARGE)
            ccl = DVCC_MAX_CHARGE_CURRENT * factor * 0.5  # Max 50% at cold temps
            return ccl, f"cold_{min_temp:.1f}C"
        
        # Hot - reduce current
        if max_temp > DVCC_TEMP_FULL_CURRENT_MAX:
            factor = 1.0 - (max_temp - DVCC_TEMP_FULL_CURRENT_MAX) / (DVCC_TEMP_STOP_CHARGE_HIGH - DVCC_TEMP_FULL_CURRENT_MAX)
            ccl = DVCC_MAX_CHARGE_CURRENT * max(0.2, factor)
            return ccl, f"hot_{max_temp:.1f}C"
        
        return DVCC_MAX_CHARGE_CURRENT, "temp_ok"
    
    def calculate_ccl_from_soc(self, soc: float) -> tuple[float, str]:
        """
        Calculate CCL based on SoC (optional battery longevity feature).
        Reduces current at high SoC to extend battery life.
        """
        if soc is None or soc < DVCC_SOC_REDUCE_START:
            return DVCC_MAX_CHARGE_CURRENT, "soc_ok"
        
        if soc >= 100.0:
            return DVCC_MAX_CHARGE_CURRENT * DVCC_SOC_REDUCE_FACTOR, "soc_100"
        
        # Linear reduction from 100% to REDUCE_FACTOR
        factor = 1.0 - (soc - DVCC_SOC_REDUCE_START) / (100.0 - DVCC_SOC_REDUCE_START) * (1.0 - DVCC_SOC_REDUCE_FACTOR)
        return DVCC_MAX_CHARGE_CURRENT * factor, f"soc_{soc:.0f}"
    
    def calculate_dcl_from_cell_voltage(self, min_cell_voltage: float) -> tuple[float, str]:
        """
        Calculate DCL based on lowest cell voltage.
        Returns (current_limit, reason_string).
        
        Protects cells from over-discharge.
        """
        if min_cell_voltage is None:
            return DVCC_MAX_DISCHARGE_CURRENT, "no_cell_data"
        
        v = min_cell_voltage
        
        # Normal voltage - full discharge
        if v >= 3.0:
            return DVCC_MAX_DISCHARGE_CURRENT, "normal"
        
        # Critical - stop discharge
        if v <= 2.7:
            return 0.0, f"cell_undervoltage_{v:.3f}V"
        
        # Reduce discharge as voltage drops
        if v <= 2.9:
            factor = (v - 2.7) / (2.9 - 2.7)
            dcl = DVCC_MAX_DISCHARGE_CURRENT * factor * 0.5
            return dcl, f"low_cell_{v:.3f}V"
        
        # Slight reduction
        factor = (v - 2.9) / (3.0 - 2.9)
        dcl = DVCC_MAX_DISCHARGE_CURRENT * (0.5 + 0.5 * factor)
        return dcl, f"reducing_{v:.3f}V"
    
    def calculate(self, data: dict) -> dict:
        """
        Calculate all DVCC parameters based on battery data.
        
        Returns dict with:
            - ccl: Charge Current Limit (A)
            - dcl: Discharge Current Limit (A)
            - cvl: Charge Voltage Limit (V)
            - ccl_reason: Why CCL was limited
            - dcl_reason: Why DCL was limited
            - max_cell_id: ID of the highest voltage cell
            - min_cell_id: ID of the lowest voltage cell
        """
        max_cell = data.get('max_cell')
        min_cell = data.get('min_cell')
        max_cell_id = data.get('max_cell_id')
        min_cell_id = data.get('min_cell_id')
        max_temp = data.get('max_temp')
        min_temp = data.get('min_temp')
        soc = data.get('soc')
        
        # Calculate cell delta (imbalance)
        cell_delta = None
        if max_cell is not None and min_cell is not None:
            cell_delta = max_cell - min_cell
        
        # Calculate CCL from all sources
        ccl_voltage, reason_voltage = self.calculate_ccl_from_cell_voltage(max_cell)
        ccl_imbalance, reason_imbalance = self.calculate_ccl_from_imbalance(cell_delta)
        ccl_temp, reason_temp = self.calculate_ccl_from_temperature(min_temp, max_temp)
        ccl_soc, reason_soc = self.calculate_ccl_from_soc(soc)
        
        # Take minimum of all CCL calculations (most restrictive wins)
        ccl_values = [
            (ccl_voltage, reason_voltage),
            (ccl_imbalance, reason_imbalance),
            (ccl_temp, reason_temp),
            (ccl_soc, reason_soc),
        ]
        
        ccl, ccl_reason = min(ccl_values, key=lambda x: x[0])
        
        # If charging is not allowed by BMS, force CCL to 0
        if not data.get('allow_charge', True):
            ccl = 0.0
            ccl_reason = "bms_blocked"
        
        # Calculate DCL
        dcl, dcl_reason = self.calculate_dcl_from_cell_voltage(min_cell)
        
        # If discharge is not allowed by BMS, force DCL to 0
        if not data.get('allow_discharge', True):
            dcl = 0.0
            dcl_reason = "bms_blocked"
        
        # Temperature-based DCL reduction
        if max_temp is not None and max_temp >= DVCC_TEMP_STOP_CHARGE_HIGH:
            dcl = min(dcl, DVCC_MAX_DISCHARGE_CURRENT * 0.5)
            dcl_reason = f"hot_{max_temp:.1f}C"
        
        # Apply rate limiting for smooth transitions
        now = time()
        dt = now - self.last_update_time
        self.last_update_time = now
        
        max_change = self.ccl_change_rate * dt
        if ccl > self.last_ccl:
            ccl = min(ccl, self.last_ccl + max_change)
        elif ccl < self.last_ccl:
            # Allow faster reduction for safety
            ccl = max(ccl, self.last_ccl - max_change * 2)
        
        self.last_ccl = ccl
        self.last_dcl = dcl
        
        # Calculate CVL (Charge Voltage Limit)
        cvl = DVCC_CELL_MAX_VOLTAGE * self.cell_count
        
        return {
            'ccl': round(ccl, 1),
            'dcl': round(dcl, 1),
            'cvl': round(cvl, 2),
            'ccl_reason': ccl_reason,
            'dcl_reason': dcl_reason,
            'max_cell_voltage': max_cell,
            'max_cell_id': max_cell_id,
            'min_cell_voltage': min_cell,
            'min_cell_id': min_cell_id,
            'cell_delta': cell_delta,
        }


# =============================================================================
# BATTERY DATA CONTAINER
# =============================================================================

class BatteryData:
    """Container for single battery data from MQTT"""
    
    def __init__(self, battery_id: int):
        self.battery_id = battery_id
        self.voltage: float = 0.0
        self.current: float = 0.0
        self.power: float = 0.0
        self.soc: float = 0.0
        self.capacity_remaining: float = 0.0
        self.capacity_total: float = 0.0  # Full capacity for time-to-go calc
        self.cycles: int = 0
        self.temperature: float = 25.0
        self.temperatures: Dict[int, float] = {}  # sensor_index -> temperature
        self.cells: Dict[int, float] = {}  # cell_index -> voltage
        self.cell_count: int = 4
        self.charging: bool = True
        self.discharging: bool = True
        self.balancing: bool = False
        self.online: bool = True
        self.last_update: float = 0.0
        self.lock = Lock()

    def update(self, key: str, value: Any):
        """Update a battery parameter"""
        with self.lock:
            if key == 'voltage':
                self.voltage = float(value)
            elif key == 'current':
                self.current = float(value)
            elif key == 'power':
                self.power = float(value)
            elif key == 'soc':
                self.soc = float(value)
            elif key == 'capacity_remaining':
                self.capacity_remaining = float(value)
            elif key == 'capacity_total':
                self.capacity_total = float(value)
            elif key == 'cycles':
                self.cycles = int(float(value))
            elif key == 'temperature':
                self.temperature = float(value)
                self.temperatures[1] = float(value)
            elif key.startswith('temperature_'):
                # temperature_1, temperature_2, etc.
                try:
                    temp_idx = int(key.split('_')[1])
                    temp_val = float(value)
                    self.temperatures[temp_idx] = temp_val
                    # Update main temperature as average
                    valid_temps = [t for t in self.temperatures.values() if t > -40]
                    if valid_temps:
                        self.temperature = sum(valid_temps) / len(valid_temps)
                except:
                    pass
            elif key == 'charging':
                self.charging = str(value).upper() in ('ON', 'TRUE', '1')
            elif key == 'discharging':
                self.discharging = str(value).upper() in ('ON', 'TRUE', '1')
            elif key == 'balancing':
                self.balancing = str(value).upper() in ('ON', 'TRUE', '1')
            elif key == 'online':
                self.online = str(value).upper() in ('ON', 'TRUE', '1')
            elif key.startswith('cell_'):
                # cell_1, cell_2, etc.
                try:
                    cell_idx = int(key.split('_')[1])
                    self.cells[cell_idx] = float(value)
                    self.cell_count = max(self.cell_count, len(self.cells))
                except:
                    pass
            self.last_update = time()
    
    def get_min_temperature(self) -> tuple[Optional[float], Optional[int]]:
        """Returns (min_temp, sensor_id)"""
        valid = [(idx, t) for idx, t in self.temperatures.items() if t > -40]
        if not valid:
            return self.temperature, 1
        min_temp = min(valid, key=lambda x: x[1])
        return min_temp[1], min_temp[0]
    
    def get_max_temperature(self) -> tuple[Optional[float], Optional[int]]:
        """Returns (max_temp, sensor_id)"""
        valid = [(idx, t) for idx, t in self.temperatures.items() if t > -40]
        if not valid:
            return self.temperature, 1
        max_temp = max(valid, key=lambda x: x[1])
        return max_temp[1], max_temp[0]

    def is_valid(self) -> bool:
        """Check if data is recent enough"""
        return (time() - self.last_update) < STALE_TIMEOUT and self.voltage > 0

    def get_min_cell_voltage(self) -> tuple[Optional[float], Optional[int]]:
        """Returns (min_voltage, cell_id)"""
        valid = [(idx, v) for idx, v in self.cells.items() if v and v > 0]
        if not valid:
            return None, None
        min_cell = min(valid, key=lambda x: x[1])
        return min_cell[1], min_cell[0]

    def get_max_cell_voltage(self) -> tuple[Optional[float], Optional[int]]:
        """Returns (max_voltage, cell_id)"""
        valid = [(idx, v) for idx, v in self.cells.items() if v and v > 0]
        if not valid:
            return None, None
        max_cell = max(valid, key=lambda x: x[1])
        return max_cell[1], max_cell[0]


# =============================================================================
# MQTT CLIENT
# =============================================================================

class MqttBatteryClient:
    """MQTT client that receives battery data from ESP32"""
    
    def __init__(self, broker: str, port: int, battery_count: int = 4, topic_prefix: str = "battery", 
                 installed_capacity: float = 280, bms_first: int = 1, cells_per_bms: int = 4):
        self.broker = broker
        self.port = port
        self.battery_count = battery_count
        self.topic_prefix = topic_prefix
        self.installed_capacity = installed_capacity  # Fixed capacity for series-connected batteries
        # MQTT topic index of first BMS for this chain (chain1: 1, chain2 with 2 BMS: 3 for bms3,bms4)
        self.bms_first = max(1, bms_first)
        # Cells per BMS module (fixed, not from MQTT to avoid issues with lost BLE connections)
        self.cells_per_bms = cells_per_bms
        
        # Create battery data containers (1-indexed for bms1, bms2, etc.)
        self.batteries: Dict[int, BatteryData] = {
            i: BatteryData(i) for i in range(1, battery_count + 1)
        }
        self._data_lock = Lock()  # Protect aggregate data access
        
        # Aggregate totals from ESP32
        self.total_voltage: float = 0.0
        self.total_current: float = 0.0
        self.total_power: float = 0.0
        self.total_soc: float = 0.0
        self.total_capacity: float = 0.0
        self.total_updated: float = 0.0
        # If ESP publishes voltage_total but never current_total, do not use total_current=0
        self.current_total_seen: bool = False
        self.soc_total_seen: bool = False
        
        # MQTT client (handle both paho-mqtt v1 and v2)
        client_id = f"dbus-mqtt-battery-{int(time())}"
        if PAHO_V2:
            self.client = mqtt.Client(
                callback_api_version=CallbackAPIVersion.VERSION1,
                client_id=client_id
            )
        else:
            self.client = mqtt.Client(client_id=client_id)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.connected = False
        self._reconnect_delay = 1  # Exponential backoff starting point
        self._max_reconnect_delay = 60

    def connect(self):
        """Connect to MQTT broker with auto-reconnect enabled"""
        try:
            logger.info(f"Connecting to MQTT broker {self.broker}:{self.port}")
            # Enable auto-reconnect with exponential backoff
            self.client.reconnect_delay_set(min_delay=1, max_delay=self._max_reconnect_delay)
            self.client.connect(self.broker, self.port, keepalive=60)
            self.client.loop_start()
            return True
        except Exception as e:
            logger.error(f"MQTT connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect from MQTT broker"""
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT connection callback"""
        if rc == 0:
            logger.info("Connected to MQTT broker")
            self.connected = True
            self._reconnect_delay = 1  # Reset backoff on successful connect
            # Subscribe to all battery topics
            topic = f"{self.topic_prefix}/#"
            client.subscribe(topic)
            logger.info(f"Subscribed to {topic}")
        else:
            logger.error(f"MQTT connection failed with code {rc}")
    
    def _on_disconnect(self, client, userdata, rc):
        """MQTT disconnection callback with auto-reconnect"""
        self.connected = False
        if rc != 0:
            logger.warning(f"MQTT disconnected unexpectedly (rc={rc}), will auto-reconnect")
        else:
            logger.info("MQTT disconnected cleanly")

    def _on_message(self, client, userdata, msg):
        """MQTT message callback"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8').strip()
            
            # Skip non-numeric payloads (like "ON"/"OFF" we handle separately)
            # Parse topic: battery/sensor/voltage_bms1/state
            #           or battery/binary_sensor/charging_bms1/state
            parts = topic.split('/')
            if len(parts) < 3:
                return
            
            sensor_type = parts[1]  # "sensor" or "binary_sensor"
            sensor_name = parts[2]  # "voltage_bms1", "voltage_total", etc.
            
            # Handle totals
            if sensor_name.endswith('_total'):
                self._update_total(sensor_name, payload)
                return
            
            # Extract battery index from name (e.g., "voltage_bms1" -> bms1 -> 1)
            match = re.search(r'bms(\d+)$', sensor_name)
            if not match:
                return
            
            bms_idx_mqtt = int(match.group(1))
            # Map MQTT bms index to internal slot (chain2: bms3,bms4 -> internal 1,2)
            bms_idx = bms_idx_mqtt - self.bms_first + 1
            if bms_idx < 1 or bms_idx > self.battery_count:
                return
            
            # Extract sensor type (e.g., "voltage_bms1" -> "voltage")
            sensor_key = re.sub(r'_bms\d+$', '', sensor_name)
            
            # Map sensor names to battery attributes
            mapping = {
                'voltage': 'voltage',
                'current': 'current',
                'power': 'power',
                'soc': 'soc',
                'capacity_remaining': 'capacity_remaining',
                'capacity': 'capacity_total',
                'cycles': 'cycles',
                'charging': 'charging',
                'discharging': 'discharging',
                'balancing': 'balancing',
                'online': 'online',
            }
            
            # Handle cell voltages: voltage_cell1 -> cell_1
            if sensor_key.startswith('voltage_cell'):
                cell_num = sensor_key.replace('voltage_cell', '')
                self.batteries[bms_idx].update(f'cell_{cell_num}', payload)
            # Handle temperature sensors: temperature1 -> temperature_1
            elif sensor_key.startswith('temperature'):
                temp_num = sensor_key.replace('temperature', '')
                if temp_num:
                    self.batteries[bms_idx].update(f'temperature_{temp_num}', payload)
                else:
                    self.batteries[bms_idx].update('temperature', payload)
            elif sensor_key in mapping:
                self.batteries[bms_idx].update(mapping[sensor_key], payload)
                
        except Exception as e:
            logger.debug(f"Error processing MQTT message: {e}")

    def _update_total(self, sensor_name: str, value: str):
        """Update aggregate totals"""
        try:
            val = float(value)
            if sensor_name == 'voltage_total':
                self.total_voltage = val
            elif sensor_name == 'current_total':
                self.total_current = val
                self.current_total_seen = True
            elif sensor_name == 'power_total':
                self.total_power = val
            elif sensor_name == 'soc_total':
                self.total_soc = val
                self.soc_total_seen = True
            elif sensor_name == 'capacity_total':
                self.total_capacity = val
            self.total_updated = time()
        except:
            pass

    def get_aggregate_data(self) -> Dict[str, Any]:
        """Get aggregated data from all batteries (thread-safe)"""
        # Copy battery data under lock to avoid race conditions with MQTT thread
        with self._data_lock:
            valid_batts = [b for b in self.batteries.values() if b.is_valid()]
            if not valid_batts:
                return None
            # Copy volatile data from each battery
            batt_snapshots = []
            for b in valid_batts:
                with b.lock:
                    batt_snapshots.append({
                        'battery_id': b.battery_id,
                        'voltage': b.voltage,
                        'current': b.current,
                        'power': b.power,
                        'soc': b.soc,
                        'capacity_remaining': b.capacity_remaining,
                        'temperature': b.temperature,
                        'temperatures': dict(b.temperatures),
                        'cells': dict(b.cells),
                        'cell_count': b.cell_count,
                        'charging': b.charging,
                        'discharging': b.discharging,
                        'cycles': b.cycles,
                        'online': b.online,
                    })
        
        # Process snapshots outside of locks
        valid_batts = batt_snapshots
        
        # Collect all cells with global IDs: (global_cell_id, voltage)
        # Global ID = (bms_id - 1) * cells_per_bms + cell_idx
        all_cells_with_id = []
        all_temps_with_id = []
        cells_per_bms = self.cells_per_bms
        
        for batt in valid_batts:
            for cell_idx, voltage in batt['cells'].items():
                if voltage and voltage > 0:
                    # Offset global IDs when this chain starts at bms N > 1
                    chain_cell_base = (self.bms_first - 1) * cells_per_bms
                    global_id = chain_cell_base + (batt['battery_id'] - 1) * cells_per_bms + cell_idx
                    all_cells_with_id.append((global_id, voltage))
            for temp_idx, temp in batt['temperatures'].items():
                if temp > -40:
                    global_id = (batt['battery_id'] - 1) * 2 + temp_idx
                    all_temps_with_id.append((global_id, temp))
        
        # Find min/max cells
        min_cell_voltage, min_cell_id = None, None
        max_cell_voltage, max_cell_id = None, None
        if all_cells_with_id:
            min_cell = min(all_cells_with_id, key=lambda x: x[1])
            max_cell = max(all_cells_with_id, key=lambda x: x[1])
            min_cell_voltage, min_cell_id = min_cell[1], min_cell[0]
            max_cell_voltage, max_cell_id = max_cell[1], max_cell[0]
        
        # Find min/max temperatures
        min_temp, min_temp_id = None, None
        max_temp, max_temp_id = None, None
        if all_temps_with_id:
            min_t = min(all_temps_with_id, key=lambda x: x[1])
            max_t = max(all_temps_with_id, key=lambda x: x[1])
            min_temp, min_temp_id = min_t[1], min_t[0]
            max_temp, max_temp_id = max_t[1], max_t[0]
        else:
            min_temp = sum(b['temperature'] for b in valid_batts) / len(valid_batts)
            max_temp = min_temp
            min_temp_id = 1
            max_temp_id = 1
        
        # Calculate capacity for SERIES-connected batteries (4S configuration)
        # In series: voltage adds, capacity stays the same
        # Use fixed installed capacity from configuration
        total_capacity_full = self.installed_capacity
        
        # Remaining capacity = installed × average SoC / 100
        avg_soc = sum(b['soc'] for b in valid_batts) / len(valid_batts)
        total_capacity_remaining = total_capacity_full * avg_soc / 100
        
        # Use ESP32 totals if available, otherwise calculate
        # Important: many ESPHome configs publish voltage_total but NOT current_total.
        # In that case total_current stays 0 and D-Bus showed 0A — use per-BMS current instead.
        if (time() - self.total_updated) < STALE_TIMEOUT and self.total_voltage > 0:
            voltage = self.total_voltage
            if self.current_total_seen:
                current = self.total_current
                power = self.total_power if self.total_power != 0 else self.total_voltage * self.total_current
            else:
                current = sum(b['current'] for b in valid_batts) / len(valid_batts)
                power = sum(b['power'] for b in valid_batts)
                if abs(power) < 1.0:
                    power = voltage * current
            if self.soc_total_seen and self.total_soc > 0:
                soc = self.total_soc
                capacity = self.total_capacity if self.total_capacity > 0 else total_capacity_remaining
            else:
                soc = min(b['soc'] for b in valid_batts)
                capacity = total_capacity_remaining
        else:
            voltage = sum(b['voltage'] for b in valid_batts)
            current = sum(b['current'] for b in valid_batts) / len(valid_batts)
            power = sum(b['power'] for b in valid_batts)
            soc = min(b['soc'] for b in valid_batts)
            capacity = total_capacity_remaining
        
        return {
            'voltage': voltage,
            'current': current,
            'power': power,
            'soc': soc,
            'capacity': capacity,
            'capacity_full': total_capacity_full,
            'min_cell': min_cell_voltage,
            'min_cell_id': min_cell_id,
            'max_cell': max_cell_voltage,
            'max_cell_id': max_cell_id,
            'min_temp': min_temp,
            'min_temp_id': min_temp_id,
            'max_temp': max_temp,
            'max_temp_id': max_temp_id,
            'temperature': sum(b['temperature'] for b in valid_batts) / len(valid_batts),
            'cell_count': sum(b['cell_count'] for b in valid_batts),
            'allow_charge': all(b['charging'] for b in valid_batts),
            'allow_discharge': all(b['discharging'] for b in valid_batts),
            'cycles': max(b['cycles'] for b in valid_batts),
            'modules_online': sum(1 for b in valid_batts if b['online']),
            'modules_offline': sum(1 for b in valid_batts if not b['online']),
            'modules_blocking_discharge': sum(1 for b in valid_batts if not b['discharging']),
            'modules_blocking_charge': sum(1 for b in valid_batts if not b['charging']),
            'all_cells': all_cells_with_id,  # List of (global_id, voltage) tuples
            'temperatures': {b['battery_id']: b['temperature'] for b in valid_batts},  # BMS ID -> temp
        }


# =============================================================================
# D-BUS SERVICE
# =============================================================================

def get_bus():
    return (
        dbus.SessionBus()
        if "DBUS_SESSION_BUS_ADDRESS" in os.environ
        else dbus.SystemBus()
    )


class DbusAggregateService:
    """D-Bus service for aggregate battery (GUI v2 compatible)"""
    
    def __init__(self, mqtt_client: MqttBatteryClient, device_instance: int = 512, 
                 service_suffix: str = "mqtt_chain", product_name: str = "JBD Battery Chain"):
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
        logger.info(f"D-Bus service registered: {service_name}")
        logger.info(f"DVCC enabled: {total_cells} cells, CVL={DVCC_CELL_MAX_VOLTAGE * total_cells:.1f}V, "
                   f"CCL max={DVCC_MAX_CHARGE_CURRENT}A, DCL max={DVCC_MAX_DISCHARGE_CURRENT}A")

    def _setup_paths(self):
        """Setup D-Bus paths for Victron GUI v2 compatibility"""
        
        # Management paths
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path("/Mgmt/ProcessVersion", VERSION)
        self._dbusservice.add_path("/Mgmt/Connection", "MQTT ESP32")
        
        # Device identification
        self._dbusservice.add_path("/DeviceInstance", self.device_instance)
        self._dbusservice.add_path("/ProductId", 0xB034)
        self._dbusservice.add_path("/ProductName", self.product_name)
        self._dbusservice.add_path("/CustomName", self.product_name, writeable=True)
        self._dbusservice.add_path("/FirmwareVersion", VERSION)
        self._dbusservice.add_path("/HardwareVersion", "ESP32 BLE Proxy")
        self._dbusservice.add_path("/Connected", 1)
        
        # DC measurements
        self._dbusservice.add_path("/Dc/0/Voltage", None, writeable=True,
            gettextcallback=lambda a, x: "{:.2f}V".format(x) if x else "")
        self._dbusservice.add_path("/Dc/0/Current", None, writeable=True,
            gettextcallback=lambda a, x: "{:.2f}A".format(x) if x else "")
        self._dbusservice.add_path("/Dc/0/Power", None, writeable=True,
            gettextcallback=lambda a, x: "{:.0f}W".format(x) if x else "")
        self._dbusservice.add_path("/Dc/0/Temperature", None, writeable=True)
        
        # State of charge
        self._dbusservice.add_path("/Soc", None, writeable=True)
        self._dbusservice.add_path("/Capacity", None, writeable=True,
            gettextcallback=lambda a, x: "{:.1f}Ah".format(x) if x else "")
        self._dbusservice.add_path("/InstalledCapacity", None, writeable=True,
            gettextcallback=lambda a, x: "{:.0f}Ah".format(x) if x else "")
        self._dbusservice.add_path("/ConsumedAmphours", None, writeable=True,
            gettextcallback=lambda a, x: "{:.1f}Ah".format(x) if x else "")
        
        # Battery system configuration (GUI v2 System menu)
        self._dbusservice.add_path("/System/NrOfBatteries", self.mqtt.battery_count, writeable=True)
        self._dbusservice.add_path("/System/BatteriesParallel", 1, writeable=True)
        self._dbusservice.add_path("/System/BatteriesSeries", self.mqtt.battery_count, writeable=True)
        self._dbusservice.add_path("/System/NrOfCellsPerBattery", self.mqtt.cells_per_bms, writeable=True)
        
        # Cell voltages (GUI v2)
        self._dbusservice.add_path("/System/MinCellVoltage", None, writeable=True,
            gettextcallback=lambda a, x: "{:.3f}V".format(x) if x else "")
        self._dbusservice.add_path("/System/MinVoltageCellId", None, writeable=True)
        self._dbusservice.add_path("/System/MaxCellVoltage", None, writeable=True,
            gettextcallback=lambda a, x: "{:.3f}V".format(x) if x else "")
        self._dbusservice.add_path("/System/MaxVoltageCellId", None, writeable=True)
        self._dbusservice.add_path("/Voltages/Sum", None, writeable=True,
            gettextcallback=lambda a, x: "{:.2f}V".format(x) if x else "")
        self._dbusservice.add_path("/Voltages/Diff", None, writeable=True,
            gettextcallback=lambda a, x: "{:.3f}V".format(x) if x else "")
        
        # Individual cell voltages for GUI v2
        # Total cells = battery_count × cells_per_battery
        total_cells = self.mqtt.battery_count * self.mqtt.cells_per_bms
        
        # Add /System/NrOfCells - required for GUI v2 to know how many cells to display
        self._dbusservice.add_path("/System/NrOfCells", total_cells, writeable=True)
        
        # GUI v2 standard paths: /Cell/{i}/Voltage (0-indexed)
        for i in range(total_cells):
            self._dbusservice.add_path(f"/Cell/{i}/Voltage", None, writeable=True,
                gettextcallback=lambda a, x: "{:.3f}V".format(x) if x else "")
            # Balancing status per cell (for color coding in GUI)
            self._dbusservice.add_path(f"/Cell/{i}/Balance", None, writeable=True)
        
        # Legacy paths for backward compatibility (dbus-serialbattery format: /Voltages/Cell1..CellN)
        for i in range(1, total_cells + 1):
            self._dbusservice.add_path(f"/Voltages/Cell{i}", None, writeable=True,
                gettextcallback=lambda a, x: "{:.3f}V".format(x) if x else "")
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
        self._dbusservice.add_path("/Info/MaxChargeCurrent", 100.0, writeable=True,
            gettextcallback=lambda a, x: "{:.1f}A".format(x) if x else "")
        self._dbusservice.add_path("/Info/MaxDischargeCurrent", 120.0, writeable=True,
            gettextcallback=lambda a, x: "{:.1f}A".format(x) if x else "")
        self._dbusservice.add_path("/Info/MaxChargeVoltage", 58.4, writeable=True,
            gettextcallback=lambda a, x: "{:.2f}V".format(x) if x else "")
        self._dbusservice.add_path("/Info/MaxChargeCellVoltage", 3.65, writeable=True,
            gettextcallback=lambda a, x: "{:.3f}V".format(x) if x else "")
        
        # IO
        self._dbusservice.add_path("/Io/AllowToCharge", 1, writeable=True)
        self._dbusservice.add_path("/Io/AllowToDischarge", 1, writeable=True)
        self._dbusservice.add_path("/Io/AllowToBalance", 1, writeable=True)
        
        # Alarms
        for alarm in ['LowVoltage', 'HighVoltage', 'LowCellVoltage', 'HighCellVoltage',
                      'LowSoc', 'HighChargeCurrent', 'HighDischargeCurrent', 
                      'CellImbalance', 'InternalFailure', 'HighTemperature', 
                      'LowTemperature', 'HighChargeTemperature', 'LowChargeTemperature']:
            self._dbusservice.add_path(f"/Alarms/{alarm}", 0, writeable=True)

    def update(self):
        """Update D-Bus values from MQTT data"""
        data = self.mqtt.get_aggregate_data()
        if not data:
            self._dbusservice["/Connected"] = 0
            return
        
        self._dbusservice["/Connected"] = 1
        
        # DC measurements
        self._dbusservice["/Dc/0/Voltage"] = round(data['voltage'], 2)
        self._dbusservice["/Dc/0/Current"] = round(data['current'], 2)
        self._dbusservice["/Dc/0/Power"] = round(data['power'], 0)
        self._dbusservice["/Dc/0/Temperature"] = round(data['temperature'], 1)
        
        # State of charge
        self._dbusservice["/Soc"] = round(data['soc'], 1)
        self._dbusservice["/Capacity"] = round(data['capacity'], 1)
        if data.get('capacity_full') and data['capacity_full'] > 0:
            self._dbusservice["/InstalledCapacity"] = round(data['capacity_full'], 0)
        
        # Time-to-go calculation (in seconds)
        current = data['current']
        capacity = data['capacity']
        capacity_full = data.get('capacity_full', 0)
        
        # Sanity check: capacity_full should be reasonable (< 10x remaining capacity)
        if capacity_full > capacity * 10 or capacity_full < capacity:
            capacity_full = capacity * 100 / max(10, data['soc'])
        
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
        total_cells = data['cell_count']
        self._dbusservice["/System/NrOfCellsPerBattery"] = self.mqtt.cells_per_bms
        
        if data['min_cell'] is not None:
            self._dbusservice["/System/MinCellVoltage"] = round(data['min_cell'], 3)
            self._dbusservice["/System/MinVoltageCellId"] = data.get('min_cell_id', 1)
        if data['max_cell'] is not None:
            self._dbusservice["/System/MaxCellVoltage"] = round(data['max_cell'], 3)
            self._dbusservice["/System/MaxVoltageCellId"] = data.get('max_cell_id', 1)
        if data['min_cell'] and data['max_cell']:
            self._dbusservice["/Voltages/Sum"] = round(data['voltage'], 2)
            self._dbusservice["/Voltages/Diff"] = round(data['max_cell'] - data['min_cell'], 3)
        
        # Update individual cell voltages for GUI v2
        all_cells = data.get('all_cells', [])
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
                except:
                    pass
        
        # Update per-battery temperatures
        temps = data.get('temperatures', {})
        for bms_id, temp in temps.items():
            if temp is not None:
                try:
                    self._dbusservice[f"/System/Temperature{bms_id}"] = round(temp, 1)
                except:
                    pass
        
        # Temperature with IDs
        if data.get('min_temp') is not None:
            self._dbusservice["/System/MinCellTemperature"] = round(data['min_temp'], 1)
            self._dbusservice["/System/MinTemperatureCellId"] = data.get('min_temp_id', 1)
        if data.get('max_temp') is not None:
            self._dbusservice["/System/MaxCellTemperature"] = round(data['max_temp'], 1)
            self._dbusservice["/System/MaxTemperatureCellId"] = data.get('max_temp_id', 1)
        
        # Modules status
        valid_count = sum(1 for b in self.mqtt.batteries.values() if b.is_valid())
        online_count = data.get('modules_online', valid_count)
        offline_count = data.get('modules_offline', 0)
        blocking_charge = data.get('modules_blocking_charge', 0)
        blocking_discharge = data.get('modules_blocking_discharge', 0)
        
        self._dbusservice["/System/NrOfModulesOnline"] = online_count
        self._dbusservice["/System/NrOfModulesOffline"] = self.mqtt.battery_count - valid_count + offline_count
        self._dbusservice["/System/NrOfModulesBlockingCharge"] = blocking_charge
        self._dbusservice["/System/NrOfModulesBlockingDischarge"] = blocking_discharge
        
        # History
        self._dbusservice["/History/ChargeCycles"] = data['cycles']
        
        # Charge/discharge control
        self._dbusservice["/Io/AllowToCharge"] = 1 if data['allow_charge'] else 0
        self._dbusservice["/Io/AllowToDischarge"] = 1 if data['allow_discharge'] else 0
        
        # Update alarms based on data
        self._update_alarms(data)
        
        # DVCC: Dynamic Voltage and Current Control
        # Calculate and publish CCL/DCL/CVL for Victron to use
        self._update_dvcc(data)

    def _update_alarms(self, data: Dict[str, Any]):
        """Update alarm states based on battery data.
        
        Alarm values: 0 = OK, 1 = Warning, 2 = Alarm/Critical
        """
        # Low SoC alarm
        soc = data.get('soc', 100)
        if soc <= ALARM_LOW_SOC_CRITICAL:
            self._dbusservice["/Alarms/LowSoc"] = 2
            logger.warning(f"ALARM: Critical Low SoC ({soc}%)")
        elif soc <= ALARM_LOW_SOC:
            self._dbusservice["/Alarms/LowSoc"] = 1
            logger.warning(f"WARNING: Low SoC ({soc}%)")
        else:
            self._dbusservice["/Alarms/LowSoc"] = 0
        
        # Low cell voltage alarm
        min_cell = data.get('min_cell')
        if min_cell is not None:
            if min_cell <= ALARM_LOW_CELL_CRITICAL:
                self._dbusservice["/Alarms/LowCellVoltage"] = 2
                logger.warning(f"ALARM: Critical Low Cell Voltage ({min_cell:.3f}V, Cell {data.get('min_cell_id', '?')})")
            elif min_cell <= ALARM_LOW_CELL_VOLTAGE:
                self._dbusservice["/Alarms/LowCellVoltage"] = 1
                logger.warning(f"WARNING: Low Cell Voltage ({min_cell:.3f}V, Cell {data.get('min_cell_id', '?')})")
            else:
                self._dbusservice["/Alarms/LowCellVoltage"] = 0
        
        # High cell voltage alarm
        max_cell = data.get('max_cell')
        if max_cell is not None:
            if max_cell >= ALARM_HIGH_CELL_CRITICAL:
                self._dbusservice["/Alarms/HighCellVoltage"] = 2
                logger.warning(f"ALARM: Critical High Cell Voltage ({max_cell:.3f}V, Cell {data.get('max_cell_id', '?')})")
            elif max_cell >= ALARM_HIGH_CELL_VOLTAGE:
                self._dbusservice["/Alarms/HighCellVoltage"] = 1
                logger.warning(f"WARNING: High Cell Voltage ({max_cell:.3f}V, Cell {data.get('max_cell_id', '?')})")
            else:
                self._dbusservice["/Alarms/HighCellVoltage"] = 0
        
        # Cell imbalance alarm
        if min_cell is not None and max_cell is not None:
            diff = max_cell - min_cell
            if diff >= ALARM_CELL_IMBALANCE * 2:
                self._dbusservice["/Alarms/CellImbalance"] = 2
                logger.warning(f"ALARM: High Cell Imbalance ({diff:.3f}V)")
            elif diff >= ALARM_CELL_IMBALANCE:
                self._dbusservice["/Alarms/CellImbalance"] = 1
            else:
                self._dbusservice["/Alarms/CellImbalance"] = 0
        
        # Temperature alarms
        max_temp = data.get('max_temp', 25)
        min_temp = data.get('min_temp', 25)
        
        # High temperature
        if max_temp >= ALARM_HIGH_TEMP_CRITICAL:
            self._dbusservice["/Alarms/HighTemperature"] = 2
            logger.warning(f"ALARM: Critical High Temperature ({max_temp}°C)")
        elif max_temp >= ALARM_HIGH_TEMP:
            self._dbusservice["/Alarms/HighTemperature"] = 1
            logger.warning(f"WARNING: High Temperature ({max_temp}°C)")
        else:
            self._dbusservice["/Alarms/HighTemperature"] = 0
        
        # Low temperature
        if min_temp <= ALARM_LOW_TEMP_CRITICAL:
            self._dbusservice["/Alarms/LowTemperature"] = 2
            logger.warning(f"ALARM: Critical Low Temperature ({min_temp}°C)")
        elif min_temp <= ALARM_LOW_TEMP:
            self._dbusservice["/Alarms/LowTemperature"] = 1
            logger.warning(f"WARNING: Low Temperature ({min_temp}°C)")
        else:
            self._dbusservice["/Alarms/LowTemperature"] = 0
        
        # BMS protection active (discharging blocked but should be discharging)
        # This indicates BMS has entered protection mode
        modules_blocking = data.get('modules_blocking_discharge', 0)
        modules_offline = data.get('modules_offline', 0)
        
        if modules_offline > 0:
            # Some modules are offline - critical alarm
            self._dbusservice["/Alarms/InternalFailure"] = 2
            logger.warning(f"ALARM: {modules_offline} module(s) OFFLINE!")
        elif modules_blocking > 0:
            # Some modules are blocking discharge - warning
            self._dbusservice["/Alarms/InternalFailure"] = 1
            logger.warning(f"WARNING: {modules_blocking} module(s) blocking discharge (BMS protection active)")
        else:
            self._dbusservice["/Alarms/InternalFailure"] = 0
        
        # Low/High voltage (aggregate)
        voltage = data.get('voltage', 0)
        cell_count = data.get('cell_count', 16)
        cell_count = 16
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

    def _update_dvcc(self, data: Dict[str, Any]):
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
        
        ccl = dvcc['ccl']
        dcl = dvcc['dcl']
        cvl = dvcc['cvl']
        ccl_reason = dvcc['ccl_reason']
        dcl_reason = dvcc['dcl_reason']
        max_cell = dvcc.get('max_cell_voltage')
        max_cell_id = dvcc.get('max_cell_id')
        min_cell = dvcc.get('min_cell_voltage')
        min_cell_id = dvcc.get('min_cell_id')
        cell_delta = dvcc.get('cell_delta')
        
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
            cell_info = f"Cell {max_cell_id}={max_cell:.3f}V" if max_cell is not None and max_cell_id is not None else ""
            
            if is_limiting and max_cell_id is not None:
                # Clear message when limiting due to cell voltage
                logger.info(f"DVCC limiting current to {ccl:.1f}A because of {cell_info}{delta_str}")
            else:
                logger.info(f"DVCC: CCL={ccl:.1f}A ({ccl_reason}), DCL={dcl:.1f}A, CVL={cvl:.1f}V, {cell_info}{delta_str}")
        
        # If CCL is critically low, log warning with cell info
        if ccl <= DVCC_MIN_CHARGE_CURRENT and ccl_reason not in ("normal", "soc_ok", "temp_ok", "balanced"):
            cell_info = f"Cell {max_cell_id} at {max_cell:.3f}V" if max_cell is not None and max_cell_id is not None else ccl_reason
            logger.warning(f"DVCC: Charge current limited to {ccl:.1f}A! Reason: {cell_info}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='MQTT to D-Bus Battery Bridge for Victron')
    parser.add_argument('--broker', default=DEFAULT_MQTT_BROKER, help='MQTT broker address')
    parser.add_argument('--port', type=int, default=DEFAULT_MQTT_PORT, help='MQTT broker port')
    parser.add_argument('--batteries', type=int, default=4, help='Number of batteries')
    parser.add_argument('--instance', type=int, default=512, help='D-Bus device instance')
    parser.add_argument('--topic-prefix', default='battery', help='MQTT topic prefix (default: battery)')
    parser.add_argument('--service-suffix', default='mqtt_chain', help='D-Bus service suffix (default: mqtt_chain)')
    parser.add_argument('--product-name', default='JBD Battery Chain', help='Product name in GUI')
    parser.add_argument('--capacity', type=float, default=280, help='Installed capacity in Ah (for series-connected batteries)')
    parser.add_argument('--cells-per-bms', type=int, default=4,
                        help='Number of cells per BMS module (default: 4 for 12V LiFePO4)')
    parser.add_argument('--bms-first', type=int, default=1,
                        help='First MQTT BMS index for this chain (chain1: 1, chain2 with bms3+bms4: 3)')
    args = parser.parse_args()

    logger.info(f"=== dbus-mqtt-battery v{VERSION} ===")
    logger.info(f"MQTT Broker: {args.broker}:{args.port}")
    logger.info(f"Topic prefix: {args.topic_prefix}")
    logger.info(f"Number of batteries: {args.batteries}, MQTT BMS index starts at: {args.bms_first}")
    logger.info(f"D-Bus service: com.victronenergy.battery.{args.service_suffix}")

    # Setup D-Bus main loop
    DBusGMainLoop(set_as_default=True)
    mainloop = gobject.MainLoop()
    
    # Variables for cleanup
    mqtt_client = None
    
    def graceful_shutdown(signum, frame):
        """Handle shutdown signals gracefully"""
        sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        mainloop.quit()
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)

    # Create MQTT client
    mqtt_client = MqttBatteryClient(
        args.broker, args.port, args.batteries, args.topic_prefix, args.capacity, 
        args.bms_first, args.cells_per_bms
    )
    if not mqtt_client.connect():
        logger.error("Failed to connect to MQTT broker")
        sys.exit(1)

    # Wait for initial data
    logger.info("Waiting for MQTT data...")
    sleep(5)

    # Create D-Bus service
    dbus_service = DbusAggregateService(mqtt_client, args.instance, args.service_suffix, args.product_name)
    
    # Periodic garbage collection counter
    gc_counter = 0
    GC_INTERVAL = 150  # Run GC every 150 polls (~5 minutes at 2s interval)

    def poll():
        """Periodic update with memory management"""
        nonlocal gc_counter
        try:
            dbus_service.update()
        except Exception as e:
            logger.error(f"Error in poll: {e}")
        
        # Periodic garbage collection for memory-constrained Venus OS
        gc_counter += 1
        if gc_counter >= GC_INTERVAL:
            gc_counter = 0
            gc.collect()
        
        return True

    # Start polling
    gobject.timeout_add(POLL_INTERVAL_MS, poll)

    logger.info("Service started, entering main loop")
    
    try:
        mainloop.run()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    except Exception as e:
        logger.error(f"Unexpected error in main loop: {e}")
    finally:
        logger.info("Cleaning up...")
        if mqtt_client:
            mqtt_client.disconnect()
        gc.collect()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
