"""Exercise new inventory through the real MQTT, DVCC and D-Bus publisher."""

import json
from types import SimpleNamespace

import pytest
from test_service_freshness import make_battery_service  # noqa: F401
from topology_samples import topology_frame, topology_manifest

from dbus_mqtt_battery.config import Config, load_config
from dbus_mqtt_battery.mqtt_client import MqttBatteryClient
from dbus_mqtt_battery.topology import parse_topology


def deliver(client, frame, *, retain=False):
    client._on_message(
        None,
        None,
        SimpleNamespace(
            topic=f"{client.topic_prefix}/telemetry",
            payload=json.dumps(frame).encode(),
            retain=retain,
        ),
    )


@pytest.mark.parametrize(
    "counts", [(4,), (4, 4), (4, 4, 4), (4, 4, 4, 4), (16,), (8, 8), (32,) * 4]
)
def test_managed_inventory_reaches_dbus_and_whole_string_veto(battery_service, counts):
    now, _, legacy_service = battery_service
    topology = parse_topology(topology_manifest(counts, managed=True))
    client = MqttBatteryClient("unused.invalid", 1883, topology=topology)
    service = type(legacy_service)(client)
    dbus = service._dbusservice
    assert dbus["/Connected"] == 0
    assert dbus["/Info/MaxChargeCurrent"] == 0
    frame = topology_frame(topology)
    deliver(client, frame, retain=True)
    assert client.get_aggregate_data() is None
    deliver(client, frame)
    service.update()
    assert dbus["/Connected"] == 1
    assert dbus["/System/NrOfCells"] == sum(counts)
    assert dbus["/Dc/0/Voltage"] == round(sum(counts) * 3.3, 2)
    assert dbus["/Dc/0/Current"] == 5.0
    assert (
        0
        < dbus["/Info/MaxChargeCurrent"]
        <= min(member.max_charge_current for member in topology.members)
    )
    for index in range(sum(counts)):
        assert dbus[f"/Cell/{index}/Voltage"] == 3.3
    assert dbus["/Members/1/BmsId"] == 3

    frame["seq"] = 2
    frame["batteries"].pop()
    deliver(client, frame)
    service.update()
    assert dbus["/Connected"] == 0
    assert dbus["/Dc/0/Voltage"] is None
    assert dbus["/Info/MaxChargeCurrent"] == 0
    assert dbus["/Info/MaxDischargeCurrent"] == 0
    deliver(client, topology_frame(topology, seq=1))
    assert client.get_aggregate_data() is None
    now[0] += 1
    deliver(client, topology_frame(topology, seq=3))
    service.update()
    assert dbus["/Connected"] == 1
    assert dbus["/Info/MaxChargeCurrent"] > 0
    now[0] += 61
    service.update()
    assert dbus["/Connected"] == 0
    assert dbus["/Info/MaxDischargeCurrent"] == 0


def test_missing_optional_capacity_remains_unknown_without_disconnecting(battery_service):
    _, _, legacy_service = battery_service
    topology = parse_topology(topology_manifest((16,), managed=True))
    client = MqttBatteryClient("unused.invalid", 1883, topology=topology)
    service = type(legacy_service)(client)
    frame = topology_frame(topology)
    deliver(client, frame)
    service.update()
    assert service._dbusservice["/Capacity"] == 38.5
    frame["seq"] = 2
    del frame["batteries"][0]["capacity"]
    deliver(client, frame)
    service.update()
    assert service._dbusservice["/Connected"] == 1
    assert service._dbusservice["/Capacity"] is None
    assert service._dbusservice["/TimeToGo"] is None


def test_mixed_inventory_offline_math_and_no_service(battery_service):
    _, _, legacy_service = battery_service
    topology = parse_topology(topology_manifest((4, 8, 4)))
    client = MqttBatteryClient("unused.invalid", 1883, topology=topology)
    frame = topology_frame(topology)
    frame["batteries"][0].update(current=4, soc=50, capacity=35)
    frame["batteries"][1].update(current=6, soc=60, capacity=40)
    frame["batteries"][2].update(current=8, soc=70, capacity=45)
    # Physical ID and packet order cannot redefine configured cell numbering.
    frame["batteries"].reverse()
    deliver(client, frame)
    data = client.get_aggregate_data()
    assert data["voltage"] == pytest.approx(52.8)
    assert data["current"] == pytest.approx(6)
    assert data["soc"] == 50
    assert data["capacity"] == 35
    assert [index for index, _ in data["all_cells"]] == list(range(1, 17))
    assert client.uniform_cells_per_bms is None
    with pytest.raises(ValueError, match="offline topology"):
        type(legacy_service)(client)
    frame["seq"] = 2
    frame["ready"] = False
    deliver(client, frame)
    assert client.get_aggregate_data() is None


@pytest.mark.parametrize(
    "event", ["disconnect", "offline", "schema_change", "invalid_cell", "mos_block"]
)
def test_topology_transport_and_physical_faults_block_control(battery_service, event):
    _, _, legacy_service = battery_service
    topology = parse_topology(topology_manifest((16,), managed=True))
    client = MqttBatteryClient("unused.invalid", 1883, topology=topology)
    service = type(legacy_service)(client)
    deliver(client, topology_frame(topology))
    service.update()
    frame = topology_frame(topology, seq=2)
    if event == "disconnect":
        client._on_disconnect(None, None, 1)
    elif event == "offline":
        client._on_message(
            None, None, SimpleNamespace(topic=f"{client.topic_prefix}/status", payload=b"offline")
        )
    else:
        if event == "schema_change":
            frame["schema"] = 1
        elif event == "invalid_cell":
            frame["batteries"][0]["cells"][0] = 0
        else:
            frame["batteries"][0]["charging"] = False
            frame["batteries"][0]["discharging"] = False
        deliver(client, frame)
    service.update()
    assert service._dbusservice["/Info/MaxChargeCurrent"] == 0
    assert service._dbusservice["/Info/MaxDischargeCurrent"] == 0


def test_topology_cli_is_explicit_and_rejects_legacy_overrides(tmp_path, monkeypatch):
    path = tmp_path / "topology.json"
    path.write_text(json.dumps(topology_manifest((16,), managed=True)))
    argv = ["service", "--topology", str(path), "--chain", "test-string", "--broker", "localhost"]
    monkeypatch.setattr("sys.argv", argv)
    config, _ = load_config()
    assert config.topology.total_cells == 16
    assert config.dbus.instance == 544
    assert config.battery.capacity == 70
    for extra in (["--batteries", "4"], ["--topic-prefix", "other"], ["--alarm-high-temp", "80"]):
        monkeypatch.setattr("sys.argv", argv + extra)
        with pytest.raises(SystemExit):
            load_config()


def test_observation_never_initializes_dbus_loop_or_battery_service(
    battery_service, monkeypatch, capsys
):
    _, _, legacy_service = battery_service
    namespace = type(legacy_service).update.__globals__
    config = Config(topology=parse_topology(topology_manifest((16,))))
    args = SimpleNamespace(validate_topology=False, observe_seconds=1)
    monkeypatch.setitem(namespace, "load_config", lambda: (config, args))

    def forbidden(*args, **kwargs):
        pytest.fail("Observation attempted to register a D-Bus battery")

    monkeypatch.setitem(namespace, "setup_main_loop", forbidden)
    monkeypatch.setitem(namespace, "DbusAggregateService", forbidden)
    monkeypatch.setattr(MqttBatteryClient, "connect", lambda self: True)
    monkeypatch.setattr(MqttBatteryClient, "disconnect", lambda self: None)
    monkeypatch.setattr("dbus_mqtt_battery.observe.observe", lambda *_: {"complete_samples": 1})
    namespace["main"]()
    assert json.loads(capsys.readouterr().out)["complete_samples"] == 1


def test_finite_but_overflowing_topology_aggregates_fail_closed(battery_service):
    _, _, legacy_service = battery_service
    topology = parse_topology(topology_manifest((16,), managed=True))
    client = MqttBatteryClient("unused.invalid", 1883, topology=topology)
    service = type(legacy_service)(client)
    frame = topology_frame(topology)
    frame["batteries"][0].update(voltage=1e308, current=1e308)
    deliver(client, frame)
    assert client.get_aggregate_data() is None
    service.update()
    assert service._dbusservice["/Connected"] == 0
    assert service._dbusservice["/Info/MaxChargeCurrent"] == 0
    assert service._dbusservice["/Info/MaxDischargeCurrent"] == 0


@pytest.mark.parametrize("remaining", [0, 1, 71])
def test_topology_time_to_go_uses_configured_rated_ah(battery_service, remaining):
    _, _, legacy_service = battery_service
    topology = parse_topology(topology_manifest((16,), managed=True))
    client = MqttBatteryClient("unused.invalid", 1883, topology=topology)
    service = type(legacy_service)(client)
    frame = topology_frame(topology)
    frame["batteries"][0].update(capacity=remaining, soc=1, current=10)
    deliver(client, frame)
    service.update()
    assert service._dbusservice["/InstalledCapacity"] == 70
    expected = int((70 - remaining) / 10 * 3600) if remaining <= 70 else None
    assert service._dbusservice["/TimeToGo"] == expected


def test_overflowing_temperature_average_blocks_topology(battery_service):
    _, _, legacy_service = battery_service
    topology = parse_topology(topology_manifest((8, 8), managed=True))
    client = MqttBatteryClient("unused.invalid", 1883, topology=topology)
    service = type(legacy_service)(client)
    frame = topology_frame(topology)
    for row in frame["batteries"]:
        row["temperature"] = 1e308
    deliver(client, frame)
    service.update()
    assert client.get_aggregate_data() is None
    assert service._dbusservice["/Info/MaxChargeCurrent"] == 0
