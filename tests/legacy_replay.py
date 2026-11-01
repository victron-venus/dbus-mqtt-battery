"""Capture deterministic D-Bus publications from an isolated legacy runtime.

Run this script in a fresh interpreter with --source pointing at the candidate,
an immutable main snapshot, or a read-only copy of the installed Cerbo source.
The runner uses fake D-Bus objects and calls MQTT callbacks without networking.
"""

# The runner deliberately exercises the same callback/publication methods as production.
# pylint: disable=protected-access,missing-function-docstring,missing-class-docstring

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import logging
import runpy
import sys
import types
from pathlib import Path

from battery_samples import deliver_frame, partial_frame, telemetry_frame

EXCLUDED_PATHS = ("/FirmwareVersion", "/Mgmt/ProcessName", "/Mgmt/ProcessVersion")
HERE = Path(__file__).resolve().parent


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def public_snapshot(value: dict) -> dict:
    return {path: item for path, item in value.items() if path not in EXCLUDED_PATHS}


class FakeDbusService(dict):
    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self.published_snapshots = []
        self.registered_snapshot = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.published_snapshots.append(public_snapshot(dict(self)))

    def add_path(self, path, value, **_kwargs):
        super().__setitem__(path, value)

    def __setitem__(self, path, value):
        if path not in self:
            raise AssertionError(f"Unregistered D-Bus path: {path}")
        super().__setitem__(path, value)

    def register(self):
        self.registered_snapshot = public_snapshot(dict(self))


def load_runtime(source: Path, clock: list[float]):
    sys.path.insert(0, str(source))
    runpy.run_path(str(HERE / "conftest.py"))
    fake_vedbus = types.ModuleType("vedbus")
    fake_vedbus.__dict__["VeDbusService"] = FakeDbusService
    sys.modules["vedbus"] = fake_vedbus
    spec = importlib.util.spec_from_file_location(
        "replayed_battery_service", source / "dbus-mqtt-battery.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load source entrypoint")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    modules = [module] + [
        importlib.import_module(name)
        for name in (
            "dvcc",
            "dbus_mqtt_battery.bms_data",
            "dbus_mqtt_battery.mqtt_client",
            "dbus_mqtt_battery.dbus_utils",
        )
    ]
    for loaded in modules:
        if hasattr(loaded, "monotonic"):
            loaded.__dict__["monotonic"] = lambda: clock[0]
        if hasattr(loaded, "time"):
            loaded.__dict__["time"] = lambda: 1700000000.0 + clock[0]
    module.__dict__["get_bus"] = lambda: None
    logging.disable(logging.CRITICAL)
    return module


def scalar(client, name: str, value, *, binary=False, retain=False):
    topic_type = "binary_sensor" if binary else "sensor"
    payload = ("ON" if value else "OFF") if binary else str(value)
    client._on_message(
        None,
        None,
        types.SimpleNamespace(
            topic=f"{client.topic_prefix}/{topic_type}/{name}/state",
            payload=payload.encode(),
            retain=retain,
        ),
    )


def legacy_frame(client, frame, *, omit_voltage=False, retain=False):
    for index, row in enumerate(frame["batteries"]):
        identifier = row["id"]
        for name in ("voltage", "current", "soc", "temperature"):
            if name == "voltage" and omit_voltage and index == 0:
                continue
            scalar(client, f"{name}_bms{identifier}", row[name], retain=retain)
        scalar(client, f"power_bms{identifier}", row["voltage"] * row["current"], retain=retain)
        for cell, value in enumerate(row["cells"], 1):
            scalar(client, f"voltage_cell{cell}_bms{identifier}", value, retain=retain)
        for name in ("online", "charging", "discharging"):
            scalar(client, f"{name}_bms{identifier}", row[name], binary=True, retain=retain)
        scalar(
            client, f"capacity_remaining_bms{identifier}", row.get("capacity", 35.0), retain=retain
        )


def replay_case(module, clock, count, first, mode):
    clock[0] = 100.0
    client = module.MqttBatteryClient(
        "unused.invalid", 1883, battery_count=count, bms_first=first, telemetry_mode=mode
    )
    service = module.DbusAggregateService(client)
    snapshots = [
        {
            "phase": "registration",
            "snapshot": service._dbusservice.registered_snapshot,
            "batches": list(service._dbusservice.published_snapshots),
        }
    ]
    service._dbusservice.published_snapshots.clear()

    def capture(label):
        service.update()
        snapshots.append(
            {
                "phase": label,
                "snapshot": public_snapshot(dict(service._dbusservice)),
                "batches": list(service._dbusservice.published_snapshots),
            }
        )
        service._dbusservice.published_snapshots.clear()

    def frame(sequence, **kwargs):
        return telemetry_frame(seq=sequence, count=count, first=first, **kwargs)

    def deliver(value, **kwargs):
        if mode == "atomic":
            deliver_frame(client, value, **kwargs)
        else:
            legacy_frame(client, value, **kwargs)

    deliver(frame(0), retain=True)
    capture("retained_does_not_initialize")
    if mode == "atomic":
        value = partial_frame(seq=0, mask=510, count=count)
        for row in value["batteries"]:
            row["id"] += first - 1
        deliver(value)
        capture("partial_does_not_initialize")
    deliver(frame(1))
    capture("healthy")
    clock[0] = 101
    zero = frame(2)
    for row in zero["batteries"]:
        row.update(current=0.0, soc=0.0, capacity=0.0)
    deliver(zero)
    capture("zero_current_soc_capacity")
    clock[0] = 102
    deliver(frame(3))
    capture("healthy_after_zero")
    clock[0] = 103
    blocked = frame(4)
    blocked["batteries"][-1].update(charging=False, discharging=False)
    deliver(blocked)
    capture("mos_veto")
    for sequence, current in ((5, 104), (6, 110)):
        clock[0] = current
        if mode == "atomic":
            partial = partial_frame(seq=sequence, mask=510, count=count)
            for row in partial["batteries"]:
                row["id"] += first - 1
            deliver(partial)
        else:
            legacy_frame(client, blocked, omit_voltage=True)
        capture(f"partial_{current}")
    clock[0] = 164
    capture("missing_field_expires")
    clock[0] = 165
    deliver(frame(7))
    capture("recovery")
    clock[0] = 166
    client._on_disconnect(None, None, 1)
    capture("transport_disconnect")
    clock[0] = 167
    client._on_connect(types.SimpleNamespace(subscribe=lambda topic: None), None, {}, 0)
    capture("transport_reconnect_without_sample")
    clock[0] = 168
    deliver(frame(8), retain=True)
    capture("retained_after_reconnect")
    clock[0] = 169
    deliver(frame(8))
    capture("live_after_reconnect")
    if mode == "atomic":
        clock[0] = 170
        restarted = frame(0, boot_id="new-producer-session")
        for row in restarted["batteries"]:
            row["age_ms"] = 5000
        deliver(restarted)
        capture("producer_reboot_preserves_sample_age")
        clock[0] = 171
        deliver(frame(99))
        capture("retired_session_replay_ignored")
        clock[0] = 172
        broken = frame(1, boot_id="new-producer-session")
        broken["batteries"][0]["voltage"] = None
        deliver(broken)
        capture("invalid_physical_field")
        clock[0] = 173
        deliver(frame(2, boot_id="new-producer-session"))
    else:
        clock[0] = 170
        for name, value in (
            ("voltage_total", 13.2 * count),
            ("current_total", 0),
            ("soc_total", 0),
            ("capacity_total", 0),
        ):
            scalar(client, name, value)
        capture("explicit_zero_aggregate_topics")
        clock[0] = 172
        scalar(client, f"voltage_bms{first}", "nan")
        capture("invalid_physical_field")
        clock[0] = 173
        deliver(frame(9))
    capture("recovery_after_invalid")
    clock[0] = 235
    capture("complete_expiry")
    clock[0] = 236
    deliver(frame(0, boot_id="third-producer-session"))
    capture("final_recovery")
    return snapshots


def capture_source(source: Path) -> dict:
    clock = [100.0]
    module = load_runtime(source, clock)
    scenarios = {}
    for count in range(1, 5):
        for first in (1, 5):
            for mode in ("atomic", "legacy"):
                scenarios[f"{mode}/{count}/first-{first}"] = replay_case(
                    module, clock, count, first, mode
                )
    files = ["dbus-mqtt-battery.py", "dvcc.py"] + [
        str(path.relative_to(source))
        for path in sorted((source / "dbus_mqtt_battery").glob("*.py"))
    ]
    return {
        "excluded_paths": list(EXCLUDED_PATHS),
        "source_files_sha256": {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in files
        },
        "scenarios": scenarios,
    }


def compact(capture: dict) -> dict:
    return {
        "excluded_paths": capture["excluded_paths"],
        "scenarios": {
            name: [
                {
                    "phase": phase["phase"],
                    "snapshot_sha256": digest(phase["snapshot"]),
                    "batches_sha256": digest(phase["batches"]),
                    "path_count": len(phase["snapshot"]),
                    "batch_count": len(phase["batches"]),
                }
                for phase in phases
            ]
            for name, phases in capture["scenarios"].items()
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    capture = capture_source(args.source.resolve(strict=True))
    print(
        json.dumps(compact(capture) if args.summary else capture, sort_keys=True, allow_nan=False)
    )


if __name__ == "__main__":
    main()
