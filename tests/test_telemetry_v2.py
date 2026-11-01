"""Variable-cell frames retain exact inventory, evidence and physical sample age."""

# pylint: disable=missing-function-docstring,redefined-outer-name

import hashlib
import json
from pathlib import Path

import pytest
from topology_samples import topology_frame, topology_manifest

from dbus_mqtt_battery.telemetry_v2 import decode_batteries, decode_header, required_mask
from dbus_mqtt_battery.topology import parse_topology


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("dbus_mqtt_battery.bms_data.monotonic", lambda: 100.0)


@pytest.mark.parametrize(
    "counts", [(4,), (4, 4), (4, 4, 4), (4,) * 4, (16,), (8, 8), (4, 8, 4), (32,) * 4]
)
def test_supported_inventory_decodes_to_stable_local_slots(counts):
    topology = parse_topology(topology_manifest(counts))
    frame = topology_frame(topology)
    frame["batteries"].reverse()
    parsed, boot, sequence = decode_header(json.dumps(frame), topology)
    batteries = decode_batteries(parsed, topology, 100)
    assert boot == frame["boot_id"] and sequence == 1
    assert tuple(batteries) == tuple(range(1, len(counts) + 1))
    assert [battery.cell_count for battery in batteries.values()] == list(counts)
    assert all(battery.is_valid() for battery in batteries.values())
    assert all(battery.oldest_sample_time == 99.75 for battery in batteries.values())


def test_bit_layout_matches_producer_and_skips_optional_fields():
    assert required_mask(1) == 15 | 256 | 16
    assert required_mask(4) == 511
    assert required_mask(5) == 511 | (1 << 11)
    assert required_mask(32) == 511 | sum(1 << bit for bit in range(11, 39))
    assert required_mask(32) & ((1 << 9) | (1 << 10) | (1 << 39)) == 0


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f.update(schema=1),
        lambda f: f.update(schema=True),
        lambda f: f.update(ready=1),
        lambda f: f.update(boot_id=""),
        lambda f: f.update(seq=True),
        lambda f: f.update(seq=-1),
        lambda f: f.update(seq=2**64),
    ],
)
def test_header_rejects_wrong_schema_and_untyped_session_metadata(change):
    topology = parse_topology(topology_manifest())
    frame = topology_frame(topology)
    change(frame)
    with pytest.raises(ValueError):
        decode_header(json.dumps(frame), topology)


def test_header_rejects_duplicate_json_keys_and_size_limit():
    topology = parse_topology(topology_manifest())
    with pytest.raises(ValueError, match="duplicate JSON"):
        decode_header('{"schema":2,"schema":1}', topology)
    with pytest.raises(ValueError, match="too large"):
        decode_header(" " * 262145, topology)


@pytest.mark.parametrize(
    "change",
    [
        lambda f: f["batteries"].pop(),
        lambda f: f["batteries"].append(f["batteries"][0].copy()),
        lambda f: f["batteries"][1].update(id=3),
        lambda f: f["batteries"][1].update(id=999),
        lambda f: f["batteries"][0].update(id=True),
        lambda f: f["batteries"][0].update(cell_count=4.0),
        lambda f: f["batteries"][0].update(reported_cell_count=3),
        lambda f: f["batteries"][0].update(reported_cell_count=True),
        lambda f: f["batteries"][0].update(required_mask=1),
        lambda f: f["batteries"][0].update(seen_mask=511.0),
        lambda f: f["batteries"][0].update(valid_mask=510),
        lambda f: f["batteries"][0]["cells"].pop(),
        lambda f: f["batteries"][0]["cells"].append(3.3),
        lambda f: f["batteries"][0]["cells"].__setitem__(0, 0),
        lambda f: f["batteries"][0]["cells"].__setitem__(0, float("nan")),
        lambda f: f["batteries"][0].update(age_ms=60000),
        lambda f: f["batteries"][0].update(age_ms=-1),
        lambda f: f["batteries"][0].update(current=float("inf")),
        lambda f: f["batteries"][0].update(soc=101),
        lambda f: f["batteries"][0].update(voltage=0),
        lambda f: f["batteries"][0].update(online=False),
        lambda f: f["batteries"][0].update(charging=1),
        lambda f: f.update(valid_mask=1),
    ],
)
def test_incomplete_or_false_evidence_cannot_produce_valid_batteries(change):
    topology = parse_topology(topology_manifest())
    frame = topology_frame(topology)
    change(frame)
    with pytest.raises(ValueError):
        decode_batteries(frame, topology, 100)


def test_not_ready_frame_builds_empty_slots_without_reusing_measurements():
    topology = parse_topology(topology_manifest())
    good = decode_batteries(topology_frame(topology), topology, 100)
    unavailable = decode_batteries(topology_frame(topology, ready=False), topology, 100)
    assert all(battery.is_valid() for battery in good.values())
    assert not any(battery.is_valid() for battery in unavailable.values())
    assert [battery.cell_count for battery in unavailable.values()] == [4, 8, 4]
    assert all(battery.voltage is None for battery in unavailable.values())


def test_schema_one_original_count_metadata_is_optional_but_checked_when_present():
    topology = parse_topology(topology_manifest((4, 4)))
    frame = topology_frame(topology)
    for row in frame["batteries"]:
        for field in ("cell_count", "reported_cell_count", "required_mask"):
            del row[field]
    assert all(b.is_valid() for b in decode_batteries(frame, topology, 100).values())
    frame["batteries"][0]["reported_cell_count"] = 5
    with pytest.raises(ValueError, match="reported_cell_count"):
        decode_batteries(frame, topology, 100)


def test_all_configured_temperatures_are_required_and_timestamped():
    document = topology_manifest((16,))
    document["chains"][0]["members"][0]["temperatures"] = 2
    topology = parse_topology(document)
    frame = topology_frame(topology)
    with pytest.raises(ValueError, match="missing configured temperatures"):
        decode_batteries(frame, topology, 100)
    frame["batteries"][0]["temperatures"] = [22.0, 24.0]
    battery = decode_batteries(frame, topology, 100)[1]
    assert battery.temperatures == {1: 22, 2: 24}
    assert battery.oldest_sample_time == 99.75
    frame["batteries"][0]["temperatures"] = [-100.0, 24.0]
    with pytest.raises(ValueError, match="temperature.*valid sensor range"):
        decode_batteries(frame, topology, 100)
    frame["batteries"][0]["temperatures"] = [22.0, 24.0]
    frame["batteries"][0]["temperature"] = -100.0
    with pytest.raises(ValueError, match="temperature.*valid sensor range"):
        decode_batteries(frame, topology, 100)


def test_zero_values_and_explicit_mos_veto_are_preserved():
    topology = parse_topology(topology_manifest((16,)))
    frame = topology_frame(topology)
    frame["batteries"][0].update(soc=0, current=0, capacity=0, cycles=0, charging=False)
    battery = decode_batteries(frame, topology, 100)[1]
    assert battery.is_valid()
    assert (battery.soc, battery.current, battery.capacity_remaining, battery.cycles) == (
        0,
        0,
        0,
        0,
    )
    assert battery.charging is False


def test_recorded_native_producer_frames_decode_without_inventory_translation():
    directory = Path(__file__).parent / "fixtures" / "series_producer"
    data = (directory / "frames.json").read_bytes()
    provenance = json.loads((directory / "provenance.json").read_text())
    assert hashlib.sha256(data).hexdigest() == provenance["frames_sha256"]
    fixtures = json.loads(data)
    assert [case["member_cell_counts"] for case in fixtures] == [
        [16],
        [4, 8, 4],
        [32, 32, 32, 32],
        [4, 4, 4, 4],
    ]
    for case in fixtures:
        document = topology_manifest(case["member_cell_counts"])
        for index, member in enumerate(document["chains"][0]["members"], 1):
            member["bms_id"] = index
        topology = parse_topology(document)
        frame, _, _ = decode_header(json.dumps(case["frame"]), topology)
        batteries = decode_batteries(frame, topology, 100)
        assert all(battery.is_valid() for battery in batteries.values())
        assert [len(battery.cells) for battery in batteries.values()] == case["member_cell_counts"]
        assert all(
            battery.oldest_sample_time == pytest.approx(99.9) for battery in batteries.values()
        )
