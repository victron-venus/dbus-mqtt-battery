"""Exercise the real aggregator, DVCC and D-Bus publisher through BMS loss."""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from battery_samples import deliver_frame, partial_frame, refresh_battery, telemetry_frame

from dbus_mqtt_battery.mqtt_client import MqttBatteryClient


class FakeDbusService(dict):
    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self.published_snapshots = []
        self.registered_snapshot = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.published_snapshots.append(dict(self))

    def add_path(self, path, value, **_kwargs):
        super().__setitem__(path, value)

    def __setitem__(self, path, value):
        assert path in self, f"Publisher wrote an unregistered path: {path}"
        super().__setitem__(path, value)

    def register(self):
        self.registered_snapshot = dict(self)


@pytest.fixture(name="battery_service")
def make_battery_service(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("dbus_mqtt_battery.bms_data.monotonic", lambda: now[0])
    monkeypatch.setattr("dbus_mqtt_battery.mqtt_client.monotonic", lambda: now[0])
    monkeypatch.setattr("dvcc.monotonic", lambda: now[0])
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
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    client = MqttBatteryClient("unused.invalid", 1883, battery_count=2)
    service = module.DbusAggregateService(client)
    return now, client, service


def refresh(battery):
    refresh_battery(battery)


def assert_unavailable(service, offline):
    published = service._dbusservice
    assert published["/Connected"] == 0
    initializing = published["/Info/TelemetryState"] == "INITIALIZING"
    assert published["/Alarms/CommunicationError"] == (1 if initializing else 2)
    assert published["/Alarms/InternalFailure"] == (0 if initializing else 2)
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
    now[0] += 1  # Charge permission recovers through the upward current ramp.
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


def test_undersized_temperature_stride_withholds_ids_on_real_export(battery_service):
    _, client, service = battery_service
    for battery in client.batteries.values():
        refresh(battery)
    client.batteries[1].update("temperature_3", 10.5)
    client.batteries[1].update("temperature_4", 30.0)
    service.update()
    paths = service._dbusservice
    assert paths["/Connected"] == 1
    assert paths["/System/MinCellTemperature"] == 10.5
    assert paths["/System/MaxCellTemperature"] == 30.0
    assert paths["/System/MinTemperatureCellId"] is None
    assert paths["/System/MaxTemperatureCellId"] is None


def test_configured_temperature_stride_exports_distinct_sensor_ids(battery_service):
    _, client, service = battery_service
    client.temps_per_bms = 4
    for battery in client.batteries.values():
        refresh(battery)
    client.batteries[1].update("temperature_4", 10.5)
    client.batteries[2].update("temperature_1", 30.0)
    service.update()
    paths = service._dbusservice
    assert paths["/System/MinCellTemperature"] == 10.5
    assert paths["/System/MaxCellTemperature"] == 30.0
    assert paths["/System/MinTemperatureCellId"] == 4
    assert paths["/System/MaxTemperatureCellId"] == 5


def test_partial_initial_data_does_not_invent_low_soc_or_activate_bms(battery_service):
    now, client, service = battery_service
    for elapsed in (0, 5, 30, 90, 180):
        now[0] = 100 + elapsed
        for battery in client.batteries.values():
            battery.update("voltage", 13.2)
        service.update()
        paths = service._dbusservice
        assert paths["/Info/TelemetryState"] == "INITIALIZING"
        assert paths["/Info/MaxChargeVoltage"] is None
        assert paths["/Info/MaxDischargeCurrent"] == 0
        assert paths["/Soc"] is None
        assert paths["/Alarms/LowSoc"] == 0
        assert paths["/Info/DataComplete"] == 0
    for battery in client.batteries.values():
        refresh(battery)
    service.update()
    assert paths["/Info/TelemetryState"] == "READY"
    assert paths["/Soc"] == 50


def test_constructor_uses_ready_snapshot_before_registration(battery_service):
    _, client, service = battery_service
    deliver_frame(client, telemetry_frame())
    restarted = type(service)(client)
    registered = restarted._dbusservice.registered_snapshot
    assert registered["/Connected"] == 1
    assert registered["/Info/TelemetryState"] == "READY"
    assert registered["/Info/MaxChargeVoltage"] == 29.2
    assert registered["/Info/MaxDischargeCurrent"] == 120
    assert registered["/Soc"] == 50


def test_atomic_status_blocks_immediately_and_survives_full_refresh(battery_service):
    _, client, service = battery_service
    frame = telemetry_frame()
    frame["batteries"][1]["discharging"] = False
    deliver_frame(client, frame)
    service.update()
    paths = service._dbusservice
    assert paths["/Connected"] == 1
    assert paths["/Info/TelemetryState"] == "PROTECTION"
    assert paths["/Io/AllowToDischarge"] == 0
    assert paths["/Info/MaxDischargeCurrent"] == 0
    assert paths["/Info/MaxChargeVoltage"] == 29.2


def test_armed_service_keeps_cvl_and_zero_dcl_after_data_loss(battery_service):
    now, client, service = battery_service
    frame = telemetry_frame()
    frame["batteries"][1]["age_ms"] = 10000
    deliver_frame(client, frame)
    service.update()
    paths = service._dbusservice
    assert paths["/Info/DataAge"] == 10
    assert paths["/Info/LastMeasurementMonotonic"] == 90
    now[0] = 151
    service.update()
    assert paths["/Info/TelemetryState"] == "STALE"
    assert paths["/Info/MaxChargeVoltage"] == 29.2
    assert paths["/Info/MaxDischargeCurrent"] == 0
    assert paths["/Info/DataComplete"] == 0
    assert paths["/System/MinCellVoltage"] is None
    assert paths["/Cell/0/Voltage"] is None
    assert paths["/Info/LastMeasurementMonotonic"] is None
    deliver_frame(client, telemetry_frame(seq=2))
    service.update()
    assert paths["/Info/TelemetryState"] == "READY"


def test_real_undervoltage_and_zero_soc_still_raise_protection(battery_service):
    _, client, service = battery_service
    frame = telemetry_frame()
    for row in frame["batteries"]:
        row.update(voltage=10.4, soc=0, cells=[2.6] * 4)
    deliver_frame(client, frame)
    service.update()
    paths = service._dbusservice
    assert paths["/Info/TelemetryState"] == "PROTECTION"
    assert paths["/Soc"] == 0
    assert paths["/Alarms/LowSoc"] == 2
    assert paths["/Alarms/LowCellVoltage"] == 2
    assert paths["/Info/MaxDischargeCurrent"] == 0


def test_failed_calculation_flushes_a_safe_complete_dbus_batch(battery_service, monkeypatch):
    _, client, service = battery_service
    deliver_frame(client, telemetry_frame())
    service.update()
    publisher = service._dbusservice
    before = len(publisher.published_snapshots)

    def fail(_data):
        raise ValueError("simulated calculation failure")

    monkeypatch.setattr(service.dvcc, "calculate", fail)
    with pytest.raises(ValueError, match="simulated calculation failure"):
        service.update()
    assert service._dbusservice is publisher
    assert len(publisher.published_snapshots) == before + 1
    published = publisher.published_snapshots[-1]
    assert published["/Connected"] == 0
    assert published["/Info/MaxDischargeCurrent"] == 0
    assert published["/Info/MaxChargeVoltage"] == 29.2
    assert published["/Soc"] is None
    assert published["/Info/TelemetryState"] == "STALE"


def test_missing_poll_is_degraded_until_original_field_expiry(battery_service):
    now, client, service = battery_service
    deliver_frame(client, telemetry_frame())
    service.update()
    paths = service._dbusservice
    limits = (paths["/Info/MaxChargeCurrent"], paths["/Info/MaxDischargeCurrent"])
    now[0] = 110
    deliver_frame(client, partial_frame())
    service.update()
    assert paths["/Info/TelemetryState"] == "DEGRADED"
    assert paths["/Info/TelemetryPartial"] == 1
    assert paths["/Info/DataComplete"] == 1
    assert paths["/Info/DataAge"] == 10
    assert paths["/Info/MissingData"]
    assert (paths["/Info/MaxChargeCurrent"], paths["/Info/MaxDischargeCurrent"]) == limits
    now[0] = 160
    service.update()
    assert paths["/Info/TelemetryState"] == "STALE"
    assert paths["/Info/MaxDischargeCurrent"] == 0
    assert paths["/Info/MaxChargeVoltage"] == 29.2
    deliver_frame(client, telemetry_frame(seq=3))
    service.update()
    assert paths["/Info/TelemetryState"] == "READY"
    assert paths["/Info/MaxDischargeCurrent"] == 120


def test_fresh_partial_low_cell_still_protects_and_cannot_regrant(battery_service):
    _, client, service = battery_service
    deliver_frame(client, telemetry_frame())
    service.update()
    frame = partial_frame(mask=16)
    frame["batteries"][0]["cells"][0] = 2.5
    deliver_frame(client, frame)
    service.update()
    paths = service._dbusservice
    assert paths["/Info/TelemetryState"] == "PROTECTION"
    assert paths["/Info/MaxDischargeCurrent"] == 0
    assert paths["/Io/AllowToDischarge"] == 0
    assert paths["/Info/TelemetryPartial"] == 1
    frame["seq"] = 3
    frame["batteries"][0]["cells"][0] = 3.3
    deliver_frame(client, frame)
    service.update()
    assert paths["/Info/MaxDischargeCurrent"] == 0
    deliver_frame(client, telemetry_frame(seq=4))
    service.update()
    assert paths["/Info/MaxDischargeCurrent"] == 120


def test_brief_broker_disconnect_does_not_interrupt_valid_bms_limits(battery_service):
    now, client, service = battery_service
    deliver_frame(client, telemetry_frame())
    service.update()
    client._on_disconnect(None, None, 1)
    now[0] = 110
    service.update()
    paths = service._dbusservice
    assert paths["/Info/TelemetryState"] == "DEGRADED"
    assert paths["/Info/DataAge"] == 10
    assert paths["/Info/MaxDischargeCurrent"] == 120
    now[0] = 160
    service.update()
    assert paths["/Info/TelemetryState"] == "STALE"
    assert paths["/Info/MaxDischargeCurrent"] == 0


@pytest.mark.parametrize("temperature", [-1, 60])
def test_partial_thermal_stop_bypasses_ramp_immediately(battery_service, temperature):
    now, client, service = battery_service
    deliver_frame(client, telemetry_frame())
    service.update()
    assert service._dbusservice["/Info/MaxChargeCurrent"] > 0
    frame = partial_frame(mask=8)
    frame["batteries"][0]["temperature"] = temperature
    now[0] += 1
    deliver_frame(client, frame)
    service.update()
    assert service._dbusservice["/Info/MaxChargeCurrent"] == 0
    assert service._dbusservice["/Io/AllowToCharge"] == 0
    assert service.dvcc.last_ccl == 0
    frame["seq"] = 3
    frame["batteries"][0]["temperature"] = 25
    deliver_frame(client, frame)
    service.update()
    assert service._dbusservice["/Info/MaxChargeCurrent"] == 0


def test_positive_thermal_derating_is_an_immediate_ceiling(battery_service):
    now, client, service = battery_service
    deliver_frame(client, telemetry_frame())
    service.update()
    frame = partial_frame(mask=8)
    frame["batteries"][0]["temperature"] = 47.5
    now[0] += 0.01
    deliver_frame(client, frame)
    service.update()
    thermal_ceiling, _ = service.dvcc.calculate_ccl_from_temperature(25, 47.5)
    assert 0 < thermal_ceiling < 100
    assert service._dbusservice["/Info/MaxChargeCurrent"] <= thermal_ceiling
    assert service.dvcc.last_ccl <= thermal_ceiling
