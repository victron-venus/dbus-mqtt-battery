"""Fuzz atomic MQTT telemetry decoding with no D-Bus/package startup."""

import math
import sys
import types
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
# The legacy package initializer imports GI/D-Bus clients. Load its real pure
# submodules as a namespace package: no production function is replaced.
package = types.ModuleType("dbus_mqtt_battery")
package.__path__ = [str(ROOT / "dbus_mqtt_battery")]
sys.modules[package.__name__] = package
with atheris.instrument_imports():
    from dbus_mqtt_battery import bms_data
    from dbus_mqtt_battery.telemetry import decode_batteries, decode_header

# The clock is the sole environmental dependency of detached battery models.
bms_data.monotonic = lambda: 10000.0


def test_one_input(data: bytes) -> None:
    try:
        frame, boot_id, sequence = decode_header(data.decode("utf-8", errors="replace"))
        batteries = decode_batteries(frame, 1, 1, 4, 10000.0)
    except ValueError:
        return
    assert boot_id and sequence >= 0  # nosec B101 - intentional test oracle
    assert set(batteries) == {1}  # nosec B101 - intentional test oracle
    battery = batteries[1]
    if frame["ready"]:
        assert battery.is_valid()  # nosec B101 - intentional test oracle
        assert 0 <= battery.soc <= 100  # nosec B101 - intentional test oracle
        for value in (battery.voltage, battery.current, battery.soc, *battery.cells.values()):
            assert math.isfinite(value)  # nosec B101 - intentional test oracle
        assert battery.last_update <= 10000.0  # nosec B101 - intentional test oracle
    else:
        assert not battery.is_valid()  # nosec B101 - intentional test oracle
        assert battery.last_update == 0  # nosec B101 - intentional test oracle


if __name__ == "__main__":
    atheris.Setup(sys.argv, atheris.instrument_func(test_one_input))
    atheris.Fuzz()
