"""Preflight rejects ambiguous or unsafe new configurations before installation."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest
from topology_samples import topology_manifest

from dbus_mqtt_battery.topology import load_manifest, load_topology, parse_manifest, parse_topology


def test_mixed_inventory_has_stable_cell_and_temperature_offsets():
    document = topology_manifest()
    document["chains"][0]["members"][1]["temperatures"] = 2
    topology = parse_topology(document)
    assert topology.member_count == 3
    assert topology.total_cells == 16
    assert topology.cell_offsets == (0, 4, 12)
    assert topology.temp_offsets == (0, 1, 3)
    assert topology.installed_capacity == 70
    assert [member.bms_id for member in topology.members] == [3, 5, 7]
    with pytest.raises(FrozenInstanceError):
        topology.members[0].cells = 8


@pytest.mark.parametrize(
    "counts", [(4,), (4, 4), (4, 4, 4), (4, 4, 4, 4), (16,), (8, 8), (32,) * 4]
)
def test_explicit_managed_profile_is_capped_by_weakest_member(counts):
    topology = parse_topology(topology_manifest(counts, managed=True))
    profile = topology.protection_profile
    assert profile.max_charge_current == 60 - (len(counts) - 1)
    assert profile.max_discharge_current == 90 - (len(counts) - 1)
    assert profile.chemistry == "lifepo4"


def test_low_member_current_also_caps_tail_current():
    document = topology_manifest((16,), managed=True)
    document["chains"][0]["members"][0]["max_charge_current"] = 1
    profile = parse_topology(document).protection_profile
    assert profile.max_charge_current == 1
    assert profile.min_charge_current <= min(
        profile.charge_balance_factor, profile.imbalance_aggressive_factor
    )


def test_managed_voltage_requires_an_explicit_compatible_equipment_limit():
    document = topology_manifest((16,), managed=True)
    del document["chains"][0]["system_voltage_limit"]
    with pytest.raises(ValueError, match="explicit system_voltage_limit"):
        parse_manifest(document)
    document["chains"][0]["system_voltage_limit"] = 48
    with pytest.raises(ValueError, match="exceeds system_voltage_limit"):
        parse_manifest(document)
    document["chains"][0]["system_voltage_limit"] = 60
    assert parse_topology(document).system_voltage_limit == 60


@pytest.mark.parametrize("field", ["capacity_ah", "max_charge_current", "max_discharge_current"])
def test_managed_requires_explicit_member_ratings(field):
    document = topology_manifest((16,), managed=True)
    del document["chains"][0]["members"][0][field]
    with pytest.raises(ValueError, match="managed members require"):
        parse_manifest(document)


def test_mixed_inventory_cannot_enable_managed_control():
    with pytest.raises(ValueError, match="equal cell counts"):
        parse_manifest(topology_manifest((4, 8, 4), managed=True))


@pytest.mark.parametrize(
    "field,value",
    [
        ("cells", True),
        ("cells", 0),
        ("cells", 33),
        ("cells", 4.0),
        ("bms_id", False),
        ("bms_id", 0),
        ("temperatures", 0),
        ("capacity_ah", float("nan")),
        ("capacity_ah", float("inf")),
        ("capacity_ah", True),
        ("max_charge_current", -1),
        ("max_discharge_current", 10**500),
    ],
)
def test_invalid_member_values_are_rejected(field, value):
    document = topology_manifest()
    document["chains"][0]["members"][0][field] = value
    with pytest.raises(ValueError):
        parse_manifest(document)


@pytest.mark.parametrize(
    "change",
    [
        lambda chain: chain.update(connection="parallel"),
        lambda chain: chain.update(output="monitor"),
        lambda chain: chain.update(service_suffix="bad; command"),
        lambda chain: chain.update(device_instance=True),
        lambda chain: chain.update(product_name="name\ncommand"),
        lambda chain: chain["source"].update(topic_prefix="battery/#"),
        lambda chain: chain["source"].update(telemetry_schema=1),
        lambda chain: chain["members"][1].update(bms_id=3),
        lambda chain: chain["members"].extend(deepcopy(chain["members"])),
        lambda chain: chain["aggregation"].update(current="sum"),
        lambda chain: chain.update(protection_profile={}),
        lambda chain: chain.update(unrecognized="setting"),
    ],
)
def test_ambiguous_or_unsupported_configurations_fail_closed(change):
    document = topology_manifest()
    change(document["chains"][0])
    with pytest.raises(ValueError):
        parse_manifest(document)


@pytest.mark.parametrize("field", ["id", "service_suffix", "device_instance", "topic_prefix"])
def test_manifest_validates_cross_chain_uniqueness(field):
    document = topology_manifest()
    second = deepcopy(document["chains"][0])
    second.update(id="second", service_suffix="second", device_instance=545)
    second["source"]["topic_prefix"] = "test/second"
    if field == "topic_prefix":
        second["source"][field] = document["chains"][0]["source"][field]
    else:
        second[field] = document["chains"][0][field]
    document["chains"].append(second)
    with pytest.raises(ValueError, match="duplicate chain"):
        parse_manifest(document)


def test_residual_battery_instance_is_reserved_unless_explicitly_reconfigured():
    document = topology_manifest()
    document["chains"][0]["device_instance"] = 514
    with pytest.raises(ValueError, match="reserved device"):
        parse_manifest(document)
    document["reserved_device_instances"] = []
    assert parse_topology(document).device_instance == 514


def test_selecting_a_chain_still_validates_the_entire_manifest():
    document = topology_manifest()
    second = deepcopy(document["chains"][0])
    second.update(id="second", service_suffix="second", device_instance=545)
    second["source"]["topic_prefix"] = "test/second"
    document["chains"].append(second)
    with pytest.raises(ValueError, match="chain id is required"):
        parse_topology(document)
    assert parse_topology(document, "second").device_instance == 545
    with pytest.raises(ValueError, match="unknown configured chain"):
        parse_topology(document, "absent")
    second["members"][0]["cells"] = 0
    with pytest.raises(ValueError):
        parse_topology(document, "test-string")


def test_file_loader_rejects_duplicates_missing_files_and_oversized_inputs(tmp_path):
    path = tmp_path / "topology.json"
    with pytest.raises(FileNotFoundError):
        load_manifest(path)
    path.write_text('{"config_version":1,"config_version":2,"chains":[]}')
    with pytest.raises(ValueError, match="duplicate JSON"):
        load_manifest(path)
    path.write_bytes(b" " * 262145)
    with pytest.raises(ValueError, match="too large"):
        load_manifest(path)
    path.write_text(json.dumps(topology_manifest()))
    assert load_topology(path).cell_offsets == (0, 4, 12)


def test_offline_capacity_remains_unknown_without_complete_ratings():
    document = topology_manifest()
    del document["chains"][0]["members"][1]["capacity_ah"]
    assert parse_topology(document).installed_capacity is None
