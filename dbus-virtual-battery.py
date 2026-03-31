#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
dbus-virtual-battery - Virtual Battery Calculator for Chain without BMS
========================================================================

Creates a virtual battery by calculating values from SmartShunt minus other chains.
Used for battery chains without physical BMS.

Architecture:
    SmartShunt (total system) - Chain1 - Chain2 = Virtual Chain3
    
    [SmartShunt] ----\
    [Chain 1]   ------ [This Script] --> D-Bus --> Victron GX
    [Chain 2]   ------/

The virtual battery inherits voltage from chain1/chain2 (parallel connection)
and calculates current as: SmartShunt_current - chain1_current - chain2_current

When any source is missing, the script shows:
- Which sources are online/offline
- Partial data where available
- Warnings in the GUI

Usage:
    ./dbus-virtual-battery.py --smartshunt ttyUSB4 --chains mqtt_chain1 mqtt_chain2
"""

import sys
import os
import argparse
import logging
from time import sleep, time
from typing import Optional, List, Dict, Tuple

# Add Victron library path
sys.path.insert(1, os.path.join(os.path.dirname(__file__), 
    "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python"))

from dbus.mainloop.glib import DBusGMainLoop
import dbus

if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject

from vedbus import VeDbusService

# Version
VERSION = "1.1.0"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)

# Poll interval
POLL_INTERVAL_MS = 2000

# Data timeout (seconds) - if no update for this long, consider source offline
DATA_TIMEOUT = 30.0

# Default battery capacity per chain (Ah) - used for SoC calculation
DEFAULT_CHAIN_CAPACITY = 280.0  # 4x 70Ah batteries in series


def get_bus():
    return (
        dbus.SessionBus()
        if "DBUS_SESSION_BUS_ADDRESS" in os.environ
        else dbus.SystemBus()
    )


class SourceStatus:
    """Track status of a data source"""
    def __init__(self, name: str, service: str):
        self.name = name
        self.service = service
        self.online = False
        self.last_seen = 0.0
        self.voltage: Optional[float] = None
        self.current: Optional[float] = None
        self.soc: Optional[float] = None
        self.power: Optional[float] = None


class DbusReader:
    """Read values from D-Bus services"""
    
    def __init__(self):
        self.bus = get_bus()
        self._cache = {}
        self._cache_time = {}
        self._cache_ttl = 1.0  # Cache values for 1 second
    
    def get_value(self, service: str, path: str) -> Optional[float]:
        """Get a value from D-Bus service"""
        cache_key = f"{service}{path}"
        now = time()
        
        # Return cached value if fresh
        if cache_key in self._cache and (now - self._cache_time.get(cache_key, 0)) < self._cache_ttl:
            return self._cache[cache_key]
        
        try:
            obj = self.bus.get_object(service, path)
            value = obj.GetValue()
            
            # Handle dbus types and empty lists
            if value is None or (isinstance(value, (list, dbus.Array)) and len(value) == 0):
                return None
            
            if hasattr(value, 'real'):
                value = float(value)
            else:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    return None
            
            self._cache[cache_key] = value
            self._cache_time[cache_key] = now
            return value
            
        except dbus.exceptions.DBusException as e:
            if "UnknownObject" not in str(e) and "NameHasNoOwner" not in str(e):
                logger.debug(f"D-Bus error reading {service}{path}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Error reading {service}{path}: {e}")
            return None
    
    def service_exists(self, service: str) -> bool:
        """Check if a D-Bus service exists"""
        try:
            self.bus.get_object(service, "/")
            return True
        except:
            return False


class VirtualBatteryService:
    """D-Bus service for virtual battery calculated from SmartShunt minus other chains"""
    
    def __init__(self, 
                 smartshunt_suffix: str,
                 chain_suffixes: List[str],
                 device_instance: int = 514,
                 product_name: str = "Virtual Battery Chain",
                 chain_capacity: float = DEFAULT_CHAIN_CAPACITY):
        
        self.dbus_reader = DbusReader()
        self.device_instance = device_instance
        self.product_name = product_name
        self.chain_capacity = chain_capacity
        self.chain_suffixes = chain_suffixes
        
        # Track data sources
        self.smartshunt = SourceStatus(
            "SmartShunt", 
            f"com.victronenergy.battery.{smartshunt_suffix}"
        )
        self.chains: List[SourceStatus] = []
        for i, suffix in enumerate(chain_suffixes):
            self.chains.append(SourceStatus(
                f"Chain{i+1}",
                f"com.victronenergy.battery.{suffix}"
            ))
        
        # Track consumed Ah for SoC calculation
        self.consumed_ah = 0.0
        self.last_update = time()
        self.initial_soc = None
        self.last_status_log = 0.0
        
        # Create D-Bus service
        service_name = "com.victronenergy.battery.virtual_chain"
        self._dbusservice = VeDbusService(service_name, get_bus(), register=False)
        
        self._setup_paths()
        self._dbusservice.register()
        logger.info(f"D-Bus service registered: {service_name}")
        logger.info(f"SmartShunt source: {self.smartshunt.service}")
        logger.info(f"Chain sources to subtract: {[c.service for c in self.chains]}")
    
    def _setup_paths(self):
        """Setup D-Bus paths for Victron GUI v2 compatibility"""
        
        # Management paths
        self._dbusservice.add_path("/Mgmt/ProcessName", __file__)
        self._dbusservice.add_path("/Mgmt/ProcessVersion", VERSION)
        self._dbusservice.add_path("/Mgmt/Connection", "Virtual (Calculated)")
        
        # Device identification
        self._dbusservice.add_path("/DeviceInstance", self.device_instance)
        self._dbusservice.add_path("/ProductId", 0xB035)
        self._dbusservice.add_path("/ProductName", self.product_name)
        self._dbusservice.add_path("/CustomName", self.product_name, writeable=True)
        self._dbusservice.add_path("/FirmwareVersion", VERSION)
        self._dbusservice.add_path("/HardwareVersion", "Virtual BMS")
        self._dbusservice.add_path("/Connected", 1)
        
        # Main battery data
        self._dbusservice.add_path("/Dc/0/Voltage", None)
        self._dbusservice.add_path("/Dc/0/Current", None)
        self._dbusservice.add_path("/Dc/0/Power", None)
        self._dbusservice.add_path("/Dc/0/Temperature", None)
        
        # Capacity and state
        self._dbusservice.add_path("/Soc", None)
        self._dbusservice.add_path("/Capacity", self.chain_capacity)
        self._dbusservice.add_path("/InstalledCapacity", self.chain_capacity)
        self._dbusservice.add_path("/ConsumedAmphours", None)
        self._dbusservice.add_path("/TimeToGo", None, writeable=True)
        
        # System info - shows source availability
        # Battery system configuration for GUI v2
        # This virtual chain represents 4 batteries in series (4S config, 48V nominal)
        self._dbusservice.add_path("/System/NrOfBatteries", 4)  # 4 batteries per chain
        self._dbusservice.add_path("/System/NrOfCellsPerBattery", 4)  # 4 cells per 12V battery
        self._dbusservice.add_path("/System/BatteriesParallel", 1)
        self._dbusservice.add_path("/System/BatteriesSeries", 4)
        
        # Modules status (sources providing data for this virtual battery)
        total_sources = 1 + len(self.chains)  # SmartShunt + other chains
        self._dbusservice.add_path("/System/NrOfModulesOnline", 0)
        self._dbusservice.add_path("/System/NrOfModulesOffline", total_sources)
        self._dbusservice.add_path("/System/NrOfModulesBlockingCharge", 0)
        self._dbusservice.add_path("/System/NrOfModulesBlockingDischarge", 0)
        
        # Cell voltage (estimated from total voltage / 16 cells)
        # Virtual battery cannot provide per-cell voltages, only estimated average
        self._dbusservice.add_path("/System/MinCellVoltage", None)
        self._dbusservice.add_path("/System/MaxCellVoltage", None)
        self._dbusservice.add_path("/System/MinVoltageCellId", "N/A (Virtual)")
        self._dbusservice.add_path("/System/MaxVoltageCellId", "N/A (Virtual)")
        
        # Estimated cell voltages (dbus-serialbattery format: /Voltages/Cell1..Cell16)
        for i in range(1, 17):
            self._dbusservice.add_path(f"/Voltages/Cell{i}", None)
        self._dbusservice.add_path("/Voltages/Sum", None)
        self._dbusservice.add_path("/Voltages/Diff", None)
        
        # Custom status info - shows which sources are online/offline
        self._dbusservice.add_path("/Info/SourceStatus", "Initializing...")
        self._dbusservice.add_path("/Info/DataComplete", 0)
        self._dbusservice.add_path("/Info/MissingSources", "")
        
        # Charge/discharge status (depends on source availability)
        self._dbusservice.add_path("/Io/AllowToCharge", 1)
        self._dbusservice.add_path("/Io/AllowToDischarge", 1)
        
        # Alarms
        self._dbusservice.add_path("/Alarms/LowVoltage", 0)
        self._dbusservice.add_path("/Alarms/HighVoltage", 0)
        self._dbusservice.add_path("/Alarms/LowSoc", 0)
        self._dbusservice.add_path("/Alarms/HighTemperature", 0)
        self._dbusservice.add_path("/Alarms/LowTemperature", 0)
        # Use InternalFailure to indicate missing data sources
        self._dbusservice.add_path("/Alarms/InternalFailure", 0)
    
    def _read_source(self, source: SourceStatus) -> bool:
        """Read data from a source and update its status. Returns True if data is valid."""
        voltage = self.dbus_reader.get_value(source.service, "/Dc/0/Voltage")
        current = self.dbus_reader.get_value(source.service, "/Dc/0/Current")
        soc = self.dbus_reader.get_value(source.service, "/Soc")
        power = self.dbus_reader.get_value(source.service, "/Dc/0/Power")
        
        now = time()
        
        # Check if we got valid data (at least voltage and current)
        if voltage is not None and current is not None:
            source.voltage = voltage
            source.current = current
            source.soc = soc
            source.power = power if power is not None else voltage * current
            source.online = True
            source.last_seen = now
            return True
        else:
            # Check if data is stale
            if source.online and (now - source.last_seen) > DATA_TIMEOUT:
                source.online = False
                logger.warning(f"{source.name} went offline (no data for {DATA_TIMEOUT}s)")
            return False
    
    def _get_status_string(self) -> Tuple[str, str, bool]:
        """Get status string showing online/offline sources.
        Returns: (status_string, missing_sources, all_online)
        """
        online = []
        offline = []
        
        if self.smartshunt.online:
            online.append("SS")
        else:
            offline.append("SmartShunt")
        
        for i, chain in enumerate(self.chains):
            if chain.online:
                online.append(f"C{i+1}")
            else:
                offline.append(f"Chain{i+1}")
        
        all_online = len(offline) == 0
        
        if all_online:
            status = f"OK: All sources online ({', '.join(online)})"
            missing = ""
        else:
            status = f"PARTIAL: Online={', '.join(online) or 'None'}"
            missing = ', '.join(offline)
        
        return status, missing, all_online
    
    def update(self):
        """Update virtual battery values"""
        now = time()
        
        # Read all sources
        self._read_source(self.smartshunt)
        for chain in self.chains:
            self._read_source(chain)
        
        # Get status
        status_str, missing_str, all_online = self._get_status_string()
        
        # Count online/offline modules
        modules_online = (1 if self.smartshunt.online else 0) + sum(1 for c in self.chains if c.online)
        modules_offline = (1 if not self.smartshunt.online else 0) + sum(1 for c in self.chains if not c.online)
        
        # Update status info
        self._dbusservice["/System/NrOfModulesOnline"] = modules_online
        self._dbusservice["/System/NrOfModulesOffline"] = modules_offline
        self._dbusservice["/Info/SourceStatus"] = status_str
        self._dbusservice["/Info/MissingSources"] = missing_str
        self._dbusservice["/Info/DataComplete"] = 1 if all_online else 0
        
        # Don't set InternalFailure alarm for missing chains - just show in status
        # Only set alarm if SmartShunt is missing (critical)
        self._dbusservice["/Alarms/InternalFailure"] = 0
        
        # Log status periodically (every 60 seconds)
        if now - self.last_status_log > 60.0:
            self.last_status_log = now
            if not all_online:
                logger.warning(f"Missing sources: {missing_str}")
            else:
                logger.info(f"All sources online")
        
        # Check if SmartShunt is available (required for any calculation)
        if not self.smartshunt.online:
            logger.debug("SmartShunt offline - cannot calculate virtual battery")
            self._dbusservice["/Connected"] = 0
            self._dbusservice["/Dc/0/Voltage"] = None
            self._dbusservice["/Dc/0/Current"] = None
            self._dbusservice["/Dc/0/Power"] = None
            self._dbusservice["/Soc"] = None
            return
        
        self._dbusservice["/Connected"] = 1
        
        # Calculate virtual battery values
        # Sum current from online chains only
        chain_current_total = 0.0
        chain_voltage_sum = 0.0
        chains_with_voltage = 0
        
        for chain in self.chains:
            if chain.online and chain.current is not None:
                chain_current_total += chain.current
            if chain.online and chain.voltage is not None and chain.voltage > 0:
                chain_voltage_sum += chain.voltage
                chains_with_voltage += 1
        
        # Calculate virtual current
        # NOTE: If some chains are offline, this will be inaccurate
        # The virtual current will include current from offline chains
        virtual_current = self.smartshunt.current - chain_current_total
        
        # Use chain voltage average for consistency (parallel connection)
        if chains_with_voltage > 0:
            virtual_voltage = chain_voltage_sum / chains_with_voltage
        else:
            # Fall back to SmartShunt voltage
            virtual_voltage = self.smartshunt.voltage
        
        virtual_power = virtual_voltage * virtual_current
        
        # Estimate cell voltage (16 cells total = 4 batteries × 4 cells per battery)
        cell_voltage = virtual_voltage / 16.0 if virtual_voltage and virtual_voltage > 0 else None
        
        # Calculate SoC as average of available chains (not SmartShunt)
        # Since all chains are in parallel, they should have similar SoC
        chain_soc_values = []
        for chain in self.chains:
            if chain.online and chain.soc is not None and chain.soc >= 0:
                chain_soc_values.append(chain.soc)
        
        if chain_soc_values:
            # Use average SoC from available chains
            virtual_soc = sum(chain_soc_values) / len(chain_soc_values)
        else:
            # Fall back to SmartShunt SoC if no chains available
            virtual_soc = self.smartshunt.soc if self.smartshunt.soc is not None else 0.0
        
        virtual_soc = max(0.0, min(100.0, virtual_soc))
        
        # Calculate consumed Ah and remaining capacity from SoC
        self.consumed_ah = self.chain_capacity * (1.0 - virtual_soc / 100.0)
        remaining_capacity = self.chain_capacity - self.consumed_ah
        self.last_update = now
        
        # Update D-Bus paths
        self._dbusservice["/Dc/0/Voltage"] = round(virtual_voltage, 2)
        self._dbusservice["/Dc/0/Current"] = round(virtual_current, 2)
        self._dbusservice["/Dc/0/Power"] = round(virtual_power, 1)
        self._dbusservice["/Soc"] = round(virtual_soc, 1)
        self._dbusservice["/Capacity"] = round(remaining_capacity, 1)
        self._dbusservice["/ConsumedAmphours"] = round(self.consumed_ah, 1)
        
        # Calculate TimeToGo (in seconds)
        if virtual_current < -0.5 and remaining_capacity > 0:
            # Discharging: time = remaining capacity / discharge current
            hours = remaining_capacity / abs(virtual_current)
            # Cap at 7 days max
            time_to_go = min(int(hours * 3600), 7 * 24 * 3600)
            self._dbusservice["/TimeToGo"] = time_to_go
        elif virtual_current > 0.5 and self.chain_capacity > remaining_capacity:
            # Charging: time = (full - remaining) / charge current
            hours = (self.chain_capacity - remaining_capacity) / virtual_current
            # Cap at 7 days max
            time_to_go = min(int(hours * 3600), 7 * 24 * 3600)
            self._dbusservice["/TimeToGo"] = time_to_go
        else:
            # Idle or very low current - no meaningful time-to-go
            self._dbusservice["/TimeToGo"] = None
        
        if cell_voltage:
            self._dbusservice["/System/MinCellVoltage"] = round(cell_voltage, 3)
            self._dbusservice["/System/MaxCellVoltage"] = round(cell_voltage, 3)
            self._dbusservice["/Voltages/Sum"] = round(virtual_voltage, 2)
            self._dbusservice["/Voltages/Diff"] = 0.0  # Virtual battery has no cell difference
            # Set all 16 cells to estimated average voltage (dbus-serialbattery format)
            for i in range(1, 17):
                self._dbusservice[f"/Voltages/Cell{i}"] = round(cell_voltage, 3)
        
        # Update CustomName to show status when sources missing
        if not all_online:
            self._dbusservice["/CustomName"] = f"{self.product_name} [Missing: {missing_str}]"
        else:
            self._dbusservice["/CustomName"] = self.product_name
        
        # Log debug info
        logger.debug(f"Virtual: {virtual_voltage:.2f}V {virtual_current:.2f}A {virtual_soc:.0f}% "
                    f"(SS: {self.smartshunt.current:.2f}A, Chains: {chain_current_total:.2f}A, "
                    f"Online: {modules_online}/{modules_online + modules_offline})")


def main():
    parser = argparse.ArgumentParser(description='Virtual Battery Calculator for Victron')
    parser.add_argument('--smartshunt', default='ttyUSB4', 
                        help='SmartShunt D-Bus service suffix (default: ttyUSB4)')
    parser.add_argument('--chains', nargs='+', default=['mqtt_chain1', 'mqtt_chain2'],
                        help='Chain D-Bus service suffixes to subtract (default: mqtt_chain1 mqtt_chain2)')
    parser.add_argument('--instance', type=int, default=514, 
                        help='D-Bus device instance (default: 514)')
    parser.add_argument('--product-name', default='Virtual Battery Chain 3',
                        help='Product name in GUI')
    parser.add_argument('--capacity', type=float, default=DEFAULT_CHAIN_CAPACITY,
                        help=f'Chain capacity in Ah (default: {DEFAULT_CHAIN_CAPACITY})')
    args = parser.parse_args()
    
    logger.info(f"=== dbus-virtual-battery v{VERSION} ===")
    logger.info(f"SmartShunt: com.victronenergy.battery.{args.smartshunt}")
    logger.info(f"Chains to subtract: {args.chains}")
    logger.info(f"Chain capacity: {args.capacity} Ah")
    
    # Setup D-Bus main loop
    DBusGMainLoop(set_as_default=True)
    mainloop = gobject.MainLoop()
    
    # Wait for services to be available
    logger.info("Waiting for D-Bus services...")
    sleep(5)
    
    # Create virtual battery service
    service = VirtualBatteryService(
        smartshunt_suffix=args.smartshunt,
        chain_suffixes=args.chains,
        device_instance=args.instance,
        product_name=args.product_name,
        chain_capacity=args.capacity
    )
    
    def poll():
        """Periodic update"""
        try:
            service.update()
        except Exception as e:
            logger.error(f"Error in poll: {e}")
        return True
    
    # Start polling
    gobject.timeout_add(POLL_INTERVAL_MS, poll)
    
    logger.info("Service started, entering main loop")
    
    try:
        mainloop.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()
