"""Explicit topology installation preflight and persistent-state regression checks."""

import json
import os
import re
import subprocess  # nosec B404 - fixture launchers only, argv stays structured
from dataclasses import asdict
from pathlib import Path

import pytest

from dbus_mqtt_battery.topology import load_manifest
from dbus_mqtt_battery.topology_install import (
    apply_plan,
    build_plan,
    require_no_owned_services,
    retire_service,
)
from dvcc import DvccProfile


def chain(identifier="test", *, instance=550, output="managed"):
    """Small complete topology suitable for an isolated installer fixture."""
    return {
        "id": identifier,
        "connection": "series",
        "source": {"topic_prefix": f"test/{identifier}", "telemetry_schema": 2},
        "members": [
            {
                "bms_id": 1,
                "cells": 16,
                "capacity_ah": 100,
                "max_charge_current": 30,
                "max_discharge_current": 40,
            }
        ],
        "service_suffix": f"mqtt_{identifier}",
        "device_instance": instance,
        "aggregation": {
            "current": "voltage_weighted_bms_estimate",
            "soc": "minimum_member",
            "remaining_capacity": "minimum_remaining_ah",
        },
        "output": output,
        "system_voltage_limit": 60.0,
        "protection_profile": asdict(DvccProfile()) if output == "managed" else None,
    }


def fixture_paths(tmp_path):
    options = tmp_path / "data/setupOptions/dbus-mqtt-battery"
    options.mkdir(parents=True)
    manifest = options / "topology.json"
    manifest.write_text(json.dumps({"config_version": 1, "chains": [chain()]}))
    kwargs = {
        "package_root": tmp_path / "data/dbus-mqtt-battery",
        "service_root": tmp_path / "service",
        "log_root": tmp_path / "var/log",
        "options_dir": options,
        "live_identities": {},
    }
    return manifest, kwargs


def test_preflight_is_read_only_and_apply_preserves_supervisors(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    plan = build_plan(manifest, **kwargs)
    package = kwargs["package_root"]
    assert not package.exists()
    apply_plan(plan)
    service = package / "service/dbus-mqtt-chain-test"
    paths = [service, service / "log"]
    for directory in [service / "supervise", service / "log/supervise"]:
        directory.mkdir()
        (directory / "lock").write_text("supervisor state")
        os.mkfifo(directory / "ok")
        paths.extend([directory, directory / "lock", directory / "ok"])
    inodes = [path.stat().st_ino for path in paths]
    kwargs["live_identities"] = {"mqtt_test": 550}
    apply_plan(build_plan(manifest, **kwargs))
    assert [path.stat().st_ino for path in paths] == inodes
    assert (kwargs["service_root"] / service.name).resolve() == service
    assert "--topology" in (service / "run").read_text()
    assert "multilog t s25000 n4" in (service / "log/run").read_text()


def test_existing_legacy_identity_conflict_leaves_launcher_unchanged(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    runner = kwargs["package_root"] / "service/dbus-mqtt-chain1/run"
    runner.parent.mkdir(parents=True)
    original = "#!/bin/sh\nexec python3 dbus-mqtt-battery.py --instance 550 --service-suffix old\n"
    runner.write_text(original)
    with pytest.raises(ValueError, match="existing unowned"):
        build_plan(manifest, **kwargs)
    assert runner.read_text() == original
    assert not (runner.parent.parent / "dbus-mqtt-chain-test").exists()


def test_live_instance_or_suffix_collision_is_rejected(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    for live in ({"other_battery": 550}, {"mqtt_test": 123}):
        kwargs["live_identities"] = live
        with pytest.raises(ValueError, match="live battery"):
            build_plan(manifest, **kwargs)
    assert not kwargs["package_root"].exists()


def test_offline_or_unchecked_plan_cannot_install(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    kwargs["live_identities"] = None
    with pytest.raises(ValueError, match="live D-Bus"):
        apply_plan(build_plan(manifest, **kwargs))
    manifest.write_text(json.dumps({"config_version": 1, "chains": [chain(output="offline")]}))
    kwargs["live_identities"] = {}
    with pytest.raises(ValueError, match="offline chains"):
        apply_plan(build_plan(manifest, **kwargs))
    assert not kwargs["package_root"].exists()


def test_manifest_mutation_and_invalid_later_chain_cannot_write(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    plan = build_plan(manifest, **kwargs)
    manifest.write_text(manifest.read_text() + "\n")
    with pytest.raises(ValueError, match="changed after preflight"):
        apply_plan(plan)
    bad = chain("second", instance=551)
    bad["members"][0]["cells"] = 0
    manifest.write_text(json.dumps({"config_version": 1, "chains": [chain(), bad]}))
    with pytest.raises(ValueError, match="cells"):
        build_plan(manifest, **kwargs)
    assert not kwargs["package_root"].exists()


def test_removed_owned_service_requires_explicit_retirement(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    manifest.write_text(
        json.dumps({"config_version": 1, "chains": [chain(), chain("second", instance=551)]})
    )
    apply_plan(build_plan(manifest, **kwargs))
    manifest.write_text(json.dumps({"config_version": 1, "chains": [chain()]}))
    with pytest.raises(ValueError, match="explicitly retire"):
        build_plan(manifest, **kwargs)
    assert (kwargs["service_root"] / "dbus-mqtt-chain-second").is_symlink()


def test_symlink_redirection_is_rejected_before_writes(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    external = tmp_path / "unrelated"
    external.mkdir()
    services = kwargs["package_root"] / "service"
    services.mkdir(parents=True)
    (services / "dbus-mqtt-chain-test").symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        build_plan(manifest, **kwargs)
    assert list(external.iterdir()) == []


def test_launcher_keeps_shell_metacharacters_as_broker_argument(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    marker = tmp_path / "must-not-exist"
    broker = "$(touch " + str(marker) + "); ' quoted"
    (kwargs["options_dir"] / "broker").write_text(broker)
    plan = build_plan(manifest, **kwargs)
    apply_plan(plan)
    output = tmp_path / "argv.json"
    dummy = kwargs["package_root"] / "dbus-mqtt-battery.py"
    dummy.write_text(
        "import json,sys\nfrom pathlib import Path\nPath("
        + repr(str(output))
        + ").write_text(json.dumps(sys.argv[1:]))\n"
    )
    runner = kwargs["package_root"] / "service/dbus-mqtt-chain-test/run"
    subprocess.run(["sh", str(runner)], check=True)  # nosec B603, B607 - isolated fixture
    argv = json.loads(output.read_text())
    assert argv[argv.index("--broker") + 1] == broker
    assert not marker.exists()


def test_configuration_cannot_live_inside_release_overlay(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    package = kwargs["package_root"]
    package.mkdir()
    embedded = package / "topology.json"
    embedded.write_text(manifest.read_text())
    with pytest.raises(ValueError, match="outside the overlaid"):
        build_plan(embedded, **kwargs)


def test_retirement_requires_owned_stopped_unlinked_service_and_preserves_state(tmp_path):
    manifest, kwargs = fixture_paths(tmp_path)
    apply_plan(build_plan(manifest, **kwargs))
    package = kwargs["package_root"]
    service_root = kwargs["service_root"]
    args = {"package_root": package, "service_root": service_root}
    with pytest.raises(ValueError, match="installer-owned"):
        retire_service("unrelated", **args)
    with pytest.raises(ValueError, match="retire every"):
        require_no_owned_services(package)
    with pytest.raises(ValueError, match="unlink"):
        retire_service("test", **args)
    (service_root / "dbus-mqtt-chain-test").unlink()
    persistent = package / "service/dbus-mqtt-chain-test"
    supervisor = persistent / "supervise"
    supervisor.mkdir()
    status = supervisor / "status"
    status.write_bytes(bytes(12) + (123).to_bytes(4, "little") + b"\0d")
    with pytest.raises(ValueError, match="child is not confirmed stopped"):
        retire_service("test", **args)
    status.write_bytes(bytes(16) + b"\0d")
    os.mkfifo(supervisor / "ok")
    reader = os.open(supervisor / "ok", os.O_RDONLY | os.O_NONBLOCK)
    try:
        with pytest.raises(ValueError, match="supervisor is still running"):
            retire_service("test", **args)
    finally:
        os.close(reader)
    inode = persistent.stat().st_ino
    retired = retire_service("test", **args)
    assert retired.stat().st_ino == inode
    assert (retired / "run").is_file()
    assert not persistent.exists()
    require_no_owned_services(package)


def test_failed_write_restores_launchers_before_new_service_links_appear(tmp_path, monkeypatch):
    from dbus_mqtt_battery import topology_install

    manifest, kwargs = fixture_paths(tmp_path)
    plan = build_plan(manifest, **kwargs)
    original = topology_install._atomic_text
    calls = 0

    def fail_registry_once(path, content, mode):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("simulated interrupted write")
        original(path, content, mode)

    monkeypatch.setattr(topology_install, "_atomic_text", fail_registry_once)
    with pytest.raises(OSError, match="simulated"):
        apply_plan(plan)
    assert not (kwargs["service_root"] / "dbus-mqtt-chain-test").exists()
    assert not kwargs["package_root"].exists()


def test_documented_examples_are_valid_complete_manifests():
    root = Path(__file__).resolve().parents[1]
    examples = sorted((root / "examples").glob("topology-*.json"))
    assert len(examples) == 3
    for example in examples:
        chains = load_manifest(example)
        assert chains
        assert all(chain.total_cells == 16 for chain in chains)


def test_boot_restores_legacy_and_ready_topology_services_only(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = (root / "setup").read_text()
    match = re.search(
        r"cat > /data/dbus-mqtt-battery/boot.sh <<'EOF'\n(.*?)\nEOF", source, re.DOTALL
    )
    assert match
    boot = tmp_path / "boot.sh"
    boot.write_text(
        match.group(1)
        .replace("/data/", str(tmp_path / "data") + "/")
        .replace('"/service/', '"' + str(tmp_path / "service") + "/")
    )
    services = tmp_path / "data/dbus-mqtt-battery/service"
    runtime = tmp_path / "service"
    runtime.mkdir()
    for name in ["dbus-mqtt-chain1", "dbus-mqtt-chain-ready", "dbus-mqtt-chain-pending"]:
        service = services / name
        service.mkdir(parents=True)
        run = service / "run"
        run.write_text("#!/bin/sh\nexit 0\n")
        run.chmod(0o755)
    (services / "dbus-mqtt-chain-ready/.topology-ready").write_text("validated\n")
    subprocess.run(["sh", str(boot)], check=True)  # nosec B603, B607 - isolated fixture
    assert (runtime / "dbus-mqtt-chain1").is_symlink()
    assert (runtime / "dbus-mqtt-chain-ready").is_symlink()
    assert not (runtime / "dbus-mqtt-chain-pending").exists()


def test_partial_activation_keeps_complete_runtime_state_for_reconciliation(tmp_path, monkeypatch):
    manifest, kwargs = fixture_paths(tmp_path)
    manifest.write_text(
        json.dumps({"config_version": 1, "chains": [chain(), chain("second", instance=551)]})
    )
    plan = build_plan(manifest, **kwargs)
    original = Path.symlink_to

    def fail_second_link(path, target, *args, **kw):
        if path.name == "dbus-mqtt-chain-second":
            raise OSError("activation failed")
        return original(path, target, *args, **kw)

    monkeypatch.setattr(Path, "symlink_to", fail_second_link)
    with pytest.raises(RuntimeError, match="preserved state"):
        apply_plan(plan)
    assert (kwargs["service_root"] / "dbus-mqtt-chain-test").is_symlink()
    assert (kwargs["package_root"] / "service/dbus-mqtt-chain-test/run").is_file()
    assert (kwargs["package_root"] / "service/dbus-mqtt-chain-second/.topology-ready").is_file()
    monkeypatch.setattr(Path, "symlink_to", original)
    kwargs["live_identities"] = {"mqtt_test": 550}
    apply_plan(build_plan(manifest, **kwargs))
    assert (kwargs["service_root"] / "dbus-mqtt-chain-second").is_symlink()


def test_broken_explicit_topology_option_never_falls_back_to_legacy(tmp_path):
    root = Path(__file__).resolve().parents[1]
    helpers = tmp_path / "data/SetupHelper/HelperResources"
    helpers.mkdir(parents=True)
    options = tmp_path / "data/setupOptions/dbus-mqtt-battery"
    options.mkdir(parents=True)
    (options / "topologyFile").symlink_to(options / "missing-option")
    (helpers / "IncludeHelpers").write_text(
        'scriptAction=INSTALL\nsetupOptionsDir="' + str(options) + '"\nendScript() { exit 0; }\n'
    )
    installer = tmp_path / "setup"
    installer.write_text(
        re.sub(
            r"(?<![\w/}])(/data|/service|/var/log)",
            lambda match: str(tmp_path) + match.group(),
            (root / "setup").read_text(),
        )
    )
    result = subprocess.run(["bash", str(installer)], capture_output=True, text=True, check=False)  # nosec B603, B607 - isolated fixture
    assert result.returncode != 0
    assert not (tmp_path / "data/dbus-mqtt-battery").exists()
