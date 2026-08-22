"""
Shared D-Bus utilities for dbus-mqtt-battery services.

Provides common functions for D-Bus setup, main loop management,
and service configuration.
"""

from __future__ import annotations

import gc
import logging
import os
import signal
import sys
from time import time
from typing import Any, Callable

import dbus

if sys.version_info.major == 2:
    import gobject
else:
    from gi.repository import GLib as gobject

from dbus.mainloop.glib import DBusGMainLoop

logger = logging.getLogger("MqttBattery")

# D-Bus DC measurement paths
PATH_DC_VOLTAGE = "/Dc/0/Voltage"
PATH_DC_CURRENT = "/Dc/0/Current"
PATH_DC_POWER = "/Dc/0/Power"

# Battery state paths
PATH_TIME_TO_GO = "/TimeToGo"


def get_bus() -> dbus.Bus:
    """Get the appropriate D-Bus (session or system)."""
    return dbus.SessionBus() if "DBUS_SESSION_BUS_ADDRESS" in os.environ else dbus.SystemBus()


def setup_main_loop() -> tuple[Any, Any]:
    """
    Set up D-Bus main loop.

    Returns:
        Tuple of (DBusGMainLoop, gobject.MainLoop)
    """
    DBusGMainLoop(set_as_default=True)
    mainloop = gobject.MainLoop()
    return mainloop


def create_shutdown_handler(mainloop: Any) -> Callable[[int, Any], None]:
    """
    Create a graceful shutdown signal handler.

    Args:
        mainloop: The main loop to quit on shutdown

    Returns:
        Signal handler function
    """

    def graceful_shutdown(signum: int, frame: Any) -> None:
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info("Received %s, shutting down gracefully...", sig_name)
        mainloop.quit()

    return graceful_shutdown


def register_signal_handlers(mainloop: Any) -> None:
    """Register SIGTERM and SIGINT handlers for graceful shutdown."""
    handler = create_shutdown_handler(mainloop)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def create_poll_function(
    service: Any,
    heartbeat_file: str | None = None,
    gc_interval: int = 150,
) -> Callable[[], bool]:
    """
    Create a periodic poll function with garbage collection.

    Args:
        service: Service object with an update() method
        heartbeat_file: Optional path to heartbeat file for watchdog
        gc_interval: How often to run GC (in poll cycles)

    Returns:
        Poll function that returns True to continue
    """
    gc_counter = 0

    def poll() -> bool:
        nonlocal gc_counter
        try:
            service.update()
        except Exception as e:
            logger.exception("Error in poll: %s", e)

        # Periodic garbage collection for memory-constrained Venus OS
        gc_counter += 1
        if gc_counter >= gc_interval:
            gc_counter = 0
            gc.collect()

        # Write heartbeat for watchdog
        if heartbeat_file:
            try:
                with open(heartbeat_file, "w", encoding="utf-8") as f:
                    f.write(str(int(time())))
            except OSError:
                pass

        return True

    return poll


def run_main_loop(
    mainloop: Any,
    poll_interval_ms: int,
    poll_fn: Callable[[], bool],
) -> None:
    """
    Start the main loop with periodic polling.

    Args:
        mainloop: The main loop to run
        poll_interval_ms: Poll interval in milliseconds
        poll_fn: Function to call periodically
    """
    gobject.timeout_add(poll_interval_ms, poll_fn)
    logger.info("Service started, entering main loop")

    try:
        mainloop.run()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    except Exception:
        logger.exception("Unexpected error in main loop")
    finally:
        gc.collect()
        logger.info("Shutdown complete")


def setup_dbus_paths_common(
    dbusservice: Any,
    process_name: str,
    version: str,
    connection: str,
    device_instance: int,
    product_name: str,
    hardware_version: str,
    product_id: int = 0xB034,
) -> None:
    """
    Setup common D-Bus paths for battery services.

    Args:
        dbusservice: VeDbusService instance
        process_name: Name of the process (usually __file__)
        version: Version string
        connection: Connection description
        device_instance: D-Bus device instance
        product_name: Product name for GUI
        hardware_version: Hardware version string
        product_id: Product ID (default: 0xB034)
    """
    # Management paths
    dbusservice.add_path("/Mgmt/ProcessName", process_name)
    dbusservice.add_path("/Mgmt/ProcessVersion", version)
    dbusservice.add_path("/Mgmt/Connection", connection)

    # Device identification
    dbusservice.add_path("/DeviceInstance", device_instance)
    dbusservice.add_path("/ProductId", product_id)
    dbusservice.add_path("/ProductName", product_name)
    dbusservice.add_path("/CustomName", product_name, writeable=True)
    dbusservice.add_path("/FirmwareVersion", version)
    dbusservice.add_path("/HardwareVersion", hardware_version)
    dbusservice.add_path("/Connected", 1)


def setup_dbus_paths_dc(
    dbusservice: Any,
    include_formats: bool = True,
) -> None:
    """
    Setup DC measurement D-Bus paths.

    Args:
        dbusservice: VeDbusService instance
        include_formats: Whether to include formatting callbacks
    """
    if include_formats:
        dbusservice.add_path(
            PATH_DC_VOLTAGE,
            None,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.2f}V" if x else "",
        )
        dbusservice.add_path(
            PATH_DC_CURRENT,
            None,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.2f}A" if x else "",
        )
        dbusservice.add_path(
            PATH_DC_POWER,
            None,
            writeable=True,
            gettextcallback=lambda a, x: f"{x:.0f}W" if x else "",
        )
    else:
        dbusservice.add_path(PATH_DC_VOLTAGE, None)
        dbusservice.add_path(PATH_DC_CURRENT, None)
        dbusservice.add_path(PATH_DC_POWER, None)
    dbusservice.add_path("/Dc/0/Temperature", None, writeable=True)


def setup_dbus_paths_alarms(dbusservice: Any) -> None:
    """
    Setup alarm D-Bus paths.

    Args:
        dbusservice: VeDbusService instance
    """
    for alarm in [
        "LowVoltage",
        "HighVoltage",
        "LowCellVoltage",
        "HighCellVoltage",
        "LowSoc",
        "HighChargeCurrent",
        "HighDischargeCurrent",
        "CellImbalance",
        "InternalFailure",
        "HighTemperature",
        "LowTemperature",
        "HighChargeTemperature",
        "LowChargeTemperature",
    ]:
        dbusservice.add_path(f"/Alarms/{alarm}", 0, writeable=True)
