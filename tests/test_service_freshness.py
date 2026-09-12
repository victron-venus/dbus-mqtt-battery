"""Exercise the real aggregator, DVCC and D-Bus publisher through BMS loss."""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access
import importlib.util
import sys
import types
from pathlib import Path

import pytest

from dbus_mqtt_battery.mqtt_client import MqttBatteryClient


class FakeDbusService(dict):
    def __init__(self, *_args, **_kwargs):
        super().__init__()

    def add_path(self, path, value, **_kwargs):
        super().__setitem__(path, value)

    def __setitem__(self, path, value):
        assert path in self, f"Publisher wrote an unregistered path: {path}"
        super().__setitem__(path, value)

    def register(self):
        assert self["/Connected"] == 0
        assert self["/Info/MaxChargeCurrent"] == 0
        assert self["/Info/MaxDischargeCurrent"] == 0


@pytest.fixture(name="battery_service")
def make_battery_service(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("dbus_mqtt_battery.bms_data.monotonic", lambda: now[0])
    monkeypatch.setattr("dbus_mqtt_battery.mqtt_client.monotonic", lambda: now[0])
    fake_vedbus = types.ModuleType("vedbus")
    fake_vedbus.VeDbusService = FakeDbusService
    monkeypatch.setitem(sys.modules, "vedbus", fake_vedbus)
    path = Path(__file__).parents[1] / "dbus-mqtt-battery.py"
    spec = importlib.util.spec_from_file_location("battery_service_under_test", path)
    module = importlib.util.module_from_spec(spec)
    # The executable adjusts sys.path for Venus OS. Restore it after loading.
    original_path = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = original_path
    monkeypatch.setattr(module, "get_bus", lambda: None)
    client = MqttBatteryClient("unused.invalid", 1883, battery_count=2)
    service = module.DbusAggregateService(client)
    return now, client, service


def refresh(battery):
    for key, value in {
        "voltage": 13.2,
        "current": 10,
        "power": 132,
        "soc": 50,
        "cell_1": 3.3,
        "temperature_1": 25,
    }.items():
        battery.update(key, value)


def assert_unavailable(service, offline):
    published = service._dbusservice
    assert published["/Connected"] == 0
    assert published["/Alarms/CommunicationError"] == 2
    assert published["/Alarms/InternalFailure"] == 2
    assert published["/System/NrOfModulesOffline"] == offline
    assert published["/System/NrOfModulesOnline"] == 2 - offline
    for path in (
        "/Io/AllowToCharge",
        "/Io/AllowToDischarge",
        "/Info/MaxChargeCurrent",
        "/Info/MaxDischargeCurrent",
    ):
        assert published[path] == 0
    for path in ("/Dc/0/Voltage", "/Dc/0/Current", "/Dc/0/Power", "/Soc"):
        assert published[path] is None


def test_missing_blocked_module_does_not_restore_charge_or_discharge(battery_service):
    now, client, service = battery_service
    assert_unavailable(service, 2)
    for battery in client.batteries.values():
        refresh(battery)
    client.batteries[2].update("charging", "OFF")
    client.batteries[2].update("discharging", "OFF")
    service.update()
    assert service._dbusservice["/Connected"] == 1
    assert service._dbusservice["/Info/MaxChargeCurrent"] == 0
    now[0] += 61
    refresh(client.batteries[1])
    # Fresh totals cannot stand in for a missing BMS in the configured series.
    client._update_total("voltage_total", "26.4")
    aggregate = client.get_aggregate_data()
    assert not aggregate["data_complete"]
    assert not aggregate["allow_charge"] and not aggregate["allow_discharge"]
    assert aggregate["modules_offline"] == 1
    assert service.dvcc.calculate(aggregate)["ccl"] == 0
    service.update()
    assert_unavailable(service, 1)
    refresh(client.batteries[2])
    service.update()
    assert service._dbusservice["/Connected"] == 1
    assert service._dbusservice["/Dc/0/Voltage"] == 26.4
    assert service._dbusservice["/Info/MaxChargeCurrent"] == 0
    assert service._dbusservice["/Info/MaxDischargeCurrent"] == 0
    assert service._dbusservice["/System/NrOfModulesOffline"] == 0
    client.batteries[2].update("charging", "ON")
    client.batteries[2].update("discharging", "ON")
    service.update()
    assert service._dbusservice["/Info/MaxChargeCurrent"] > 0
    assert service._dbusservice["/Info/MaxDischargeCurrent"] > 0


def test_all_lost_or_explicitly_offline_modules_fail_closed(battery_service):
    now, client, service = battery_service
    for battery in client.batteries.values():
        refresh(battery)
    service.update()
    client.batteries[2].update("online", "OFF")
    service.update()
    assert_unavailable(service, 1)
    now[0] += 61
    for battery in client.batteries.values():
        battery.update("online", "ON")
    service.update()
    assert_unavailable(service, 2)


def test_soc_warning_logs_transitions_and_two_minute_reminders(
    battery_service, monkeypatch, caplog
):
    _clock, _client, service = battery_service
    now = [1000.0]
    monkeypatch.setitem(service._update_soc_alarm.__globals__, "time", lambda: now[0])
    with caplog.at_level("INFO", logger="MqttBattery"):
        for _ in range(60):
            service._update_soc_alarm({"soc": 10})
            now[0] += 2
        assert len(caplog.records) == 1
        service._update_soc_alarm({"soc": 10})
        assert len(caplog.records) == 2
        service._update_soc_alarm({"soc": 1})
        assert service._dbusservice["/Alarms/LowSoc"] == 2
        assert len(caplog.records) == 3
        service._update_soc_alarm({"soc": 80})
        assert service._dbusservice["/Alarms/LowSoc"] == 0
        assert "cleared" in caplog.records[-1].message
