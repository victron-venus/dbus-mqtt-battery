"""Preflight and install explicitly configured series services without restarting them."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path

# Executing this file must not import the package's D-Bus runtime before preflight.
if not __package__:
    _root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_root))
    _package = types.ModuleType("dbus_mqtt_battery")
    _package.__path__ = [str(_root / "dbus_mqtt_battery")]
    sys.modules["dbus_mqtt_battery"] = _package

from dbus_mqtt_battery.topology import load_manifest


@dataclass(frozen=True)
class ServicePlan:
    """One validated service and its fully quoted launcher."""

    name: str
    chain_id: str
    suffix: str
    instance: int
    cells: tuple[int, ...]
    output: str
    charge_voltage_limit: float | None
    system_voltage_limit: float | None
    run: str
    log_run: str


@dataclass(frozen=True)
class InstallPlan:
    """Complete plan; construction does not mutate installation state."""

    manifest: Path
    manifest_sha256: str
    package_root: Path
    service_root: Path
    log_root: Path
    services: tuple[ServicePlan, ...]
    live_checked: bool

    def summary(self) -> dict:
        """Describe topology changes without printing broker settings or launcher text."""
        return {
            "manifest": str(self.manifest),
            "manifest_sha256": self.manifest_sha256,
            "live_identities_checked": self.live_checked,
            "services": [
                {
                    "name": service.name,
                    "chain": service.chain_id,
                    "dbus_suffix": service.suffix,
                    "device_instance": service.instance,
                    "member_cells": service.cells,
                    "output": service.output,
                    "charge_voltage_limit": service.charge_voltage_limit,
                    "system_voltage_limit": service.system_voltage_limit,
                }
                for service in self.services
            ],
            "restarts": "Existing services are not restarted by this installer",
        }


def _registry(package_root: Path) -> dict:
    path = package_root / "topology-services.json"
    if path.is_symlink():
        raise ValueError("topology ownership registry must not be a symlink")
    if not path.exists():
        return {}
    document = json.loads(path.read_text())
    if (
        not isinstance(document, dict)
        or document.get("config_version") != 1
        or not isinstance(document.get("services"), dict)
    ):
        raise ValueError("invalid topology ownership registry")
    services = document["services"]
    if any(
        re.fullmatch(r"dbus-mqtt-chain-[a-z][a-z0-9_-]{0,63}", name) is None
        or not isinstance(value, dict)
        for name, value in services.items()
    ):
        raise ValueError("invalid owned topology service name")
    return services


def _runner_identity(path: Path) -> tuple[str | None, int | None]:
    """Read legacy launcher argv as data; never execute or source a launcher."""
    tokens = shlex.split(path.read_text())
    result: list[str | int | None] = []
    for flag in ("--service-suffix", "--instance"):
        try:
            value = tokens[tokens.index(flag) + 1]
            result.append(int(value) if flag == "--instance" else value)
        except (ValueError, IndexError):
            result.append(None)
    return result[0], result[1]  # type: ignore[return-value]


def live_battery_identities() -> dict[str, int]:
    """Read live battery identities; inability to complete the check aborts apply."""
    import dbus  # Imported only for explicitly requested live preflight or apply.

    try:
        bus = dbus.SystemBus()
        result = {}
        for name in bus.list_names():
            if not name.startswith("com.victronenergy.battery."):
                continue
            item = bus.get_object(name, "/DeviceInstance")
            instance = dbus.Interface(item, "com.victronenergy.BusItem").GetValue(timeout=5)
            result[str(name).removeprefix("com.victronenergy.battery.")] = int(instance)
        return result
    except Exception as error:
        raise RuntimeError(
            "live battery identity check failed; retry after D-Bus is available"
        ) from error


def _check_paths(package_root: Path, service_root: Path, log_root: Path, names: set[str]) -> None:
    """Reject paths that could redirect writes or replace an unrelated service."""
    if package_root.is_symlink() or (package_root / "service").is_symlink():
        raise ValueError("package and persistent service roots must not be symlinks")
    for name in names:
        target = package_root / "service" / name
        for path in (
            target,
            target / "log",
            target / "run",
            target / "log/run",
            target / ".topology-ready",
            log_root / name,
        ):
            if path.is_symlink():
                raise ValueError(f"refusing symlink in installation path: {path}")
        for path in (target / "run", target / "log/run", target / ".topology-ready"):
            if path.exists() and not path.is_file():
                raise ValueError(f"launcher path is not a regular file: {path}")
        for path in (target, target / "log", log_root / name):
            if path.exists() and not path.is_dir():
                raise ValueError(f"service path is not a directory: {path}")
        link = service_root / name
        if link.is_symlink():
            if link.resolve() != target.resolve():
                raise ValueError(f"service link belongs to another installation: {name}")
        elif link.exists():
            raise ValueError(f"refusing to replace a real supervised service directory: {name}")


def build_plan(
    manifest: Path,
    *,
    package_root: Path = Path("/data/dbus-mqtt-battery"),
    service_root: Path = Path("/service"),
    log_root: Path = Path("/var/log"),
    options_dir: Path = Path("/data/setupOptions/dbus-mqtt-battery"),
    live_identities: dict[str, int] | None = None,
) -> InstallPlan:
    """Validate every member and collision before returning any writable plan."""
    if not manifest.is_absolute() or manifest.is_symlink() or not manifest.is_file():
        raise ValueError("topologyFile must name an absolute regular file, not a symlink")
    manifest = manifest.resolve()
    if manifest.is_relative_to(package_root.resolve()):
        raise ValueError("keep topology configuration outside the overlaid package directory")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    chains = load_manifest(manifest)
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != digest:
        raise ValueError("topology changed during validation")
    owned = _registry(package_root)
    services = []
    broker_path = options_dir / "broker"
    broker = broker_path.read_text().strip() if broker_path.exists() else "127.0.0.1"
    if not broker or "\n" in broker or "\x00" in broker:
        raise ValueError("broker must be one nonempty value")
    for chain in chains:
        name = f"dbus-mqtt-chain-{chain.id}"
        argv = [
            "python3",
            "dbus-mqtt-battery.py",
            "--broker",
            broker,
            "--topology",
            str(manifest),
            "--chain",
            chain.id,
        ]
        services.append(
            ServicePlan(
                name,
                chain.id,
                chain.service_suffix,
                chain.device_instance,
                tuple(member.cells for member in chain.members),
                chain.output,
                (
                    chain.protection_profile.cell_max_voltage * chain.total_cells
                    if chain.protection_profile
                    else None
                ),
                chain.system_voltage_limit,
                "#!/bin/sh\nexec 2>&1\ncd "
                + shlex.quote(str(package_root))
                + " || exit 1\nexec "
                + shlex.join(argv)
                + "\n",
                "#!/bin/sh\nexec 2>&1\nexec "
                + shlex.join(["multilog", "t", "s25000", "n4", str(log_root / name)])
                + "\n",
            )
        )
    names = {service.name for service in services}
    retired = set(owned) - names
    if retired:
        raise ValueError(
            "explicitly retire removed topology services before applying: "
            + ", ".join(sorted(retired))
        )
    _check_paths(package_root, service_root, log_root, names)
    for runner in (package_root / "service").glob("*/run"):
        if runner.parent.name in owned:
            continue
        suffix, instance = _runner_identity(runner)
        for service in services:
            if (
                runner.parent.name == service.name
                or suffix == service.suffix
                or instance == service.instance
            ):
                raise ValueError(
                    f"identity conflicts with existing unowned service {runner.parent.name}"
                )
    if live_identities is not None:
        for suffix, instance in live_identities.items():
            for service in services:
                owner = owned.get(service.name, {})
                same_owned = (
                    owner.get("service_suffix") == suffix == service.suffix
                    and owner.get("device_instance") == instance == service.instance
                )
                if (suffix == service.suffix or instance == service.instance) and not same_owned:
                    raise ValueError(f"identity conflicts with live battery service {suffix}")
    return InstallPlan(
        manifest,
        digest,
        package_root,
        service_root,
        log_root,
        tuple(services),
        live_identities is not None,
    )


def _atomic_text(path: Path, text: str, mode: int) -> None:
    """Replace one owned regular file without truncating a launcher in place."""
    descriptor, temporary = tempfile.mkstemp(prefix=".topology-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_plan(plan: InstallPlan) -> None:
    """Install only a validated managed plan; preserve supervised directories."""
    if any(service.output != "managed" for service in plan.services):
        raise ValueError("offline chains cannot be installed as supervised battery services")
    if not plan.live_checked:
        raise ValueError("apply requires a successful live D-Bus identity check")
    if hashlib.sha256(plan.manifest.read_bytes()).hexdigest() != plan.manifest_sha256:
        raise ValueError("topology changed after preflight; build a new plan")
    _check_paths(
        plan.package_root,
        plan.service_root,
        plan.log_root,
        {service.name for service in plan.services},
    )
    files: dict[Path, tuple[str, int]] = {}
    for service in plan.services:
        target = plan.package_root / "service" / service.name
        files[target / "run"] = (service.run, 0o755)
        files[target / "log/run"] = (service.log_run, 0o755)
    registry = {
        "config_version": 1,
        "manifest": str(plan.manifest),
        "manifest_sha256": plan.manifest_sha256,
        "services": {
            service.name: {
                "chain_id": service.chain_id,
                "service_suffix": service.suffix,
                "device_instance": service.instance,
            }
            for service in plan.services
        },
    }
    files[plan.package_root / "topology-services.json"] = (
        json.dumps(registry, indent=2) + "\n",
        0o600,
    )
    for service in plan.services:
        files[plan.package_root / "service" / service.name / ".topology-ready"] = (
            plan.manifest_sha256 + "\n",
            0o600,
        )
    previous = {
        path: (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
        for path in files
    }
    written: list[Path] = []
    links: list[Path] = []
    directories: list[Path] = []

    def ensure_directory(path: Path) -> None:
        missing = []
        current = path
        while not current.exists():
            missing.append(current)
            current = current.parent
        path.mkdir(parents=True, exist_ok=True)
        directories.extend(reversed(missing))

    try:
        ensure_directory(plan.service_root)
        for service in plan.services:
            ensure_directory(plan.log_root / service.name)
        # Every launcher is complete before readiness markers and links appear.
        # Existing supervisors and processes are never restarted or replaced.
        for path, (content, mode) in files.items():
            ensure_directory(path.parent)
            written.append(path)
            _atomic_text(path, content, mode)
        for service in plan.services:
            link = plan.service_root / service.name
            if not link.is_symlink():
                link.symlink_to(plan.package_root / "service" / service.name)
                links.append(link)
    except OSError as error:
        if links:
            # svscan may already have started a newly linked service. Preserve
            # its complete runtime paths; another preflight/apply reconciles links.
            raise RuntimeError(
                "service activation interrupted after launchers completed; "
                "preserved state requires another preflight/apply"
            ) from error
        for path in reversed(written):
            old = previous[path]
            if old is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_text(path, old[0], old[1])
        for directory in reversed(directories):
            directory.rmdir()
        raise


def _require_quiescent(service: Path) -> None:
    """Require stopped child and supervisor before moving their persistent paths."""
    for directory in (service / "supervise", service / "log/supervise"):
        status = directory / "status"
        ok = directory / "ok"
        if directory.is_symlink() or status.is_symlink() or ok.is_symlink():
            raise ValueError("supervisor state must not be symlinked for retirement")
        if status.exists():
            data = status.read_bytes()
            # daemontools 18-byte status: TAI64N[12], little-endian child PID[4], flags[2].
            if len(data) != 18 or int.from_bytes(data[12:16], "little") != 0:
                raise ValueError(f"service child is not confirmed stopped: {service.name}")
        try:
            descriptor = os.open(ok, os.O_WRONLY | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        except OSError as error:
            if error.errno == errno.ENXIO:
                continue
            raise
        else:
            os.close(descriptor)
            raise ValueError(f"supervisor is still running: {service.name}")


def retire_service(
    chain_id: str,
    *,
    package_root: Path = Path("/data/dbus-mqtt-battery"),
    service_root: Path = Path("/service"),
) -> Path:
    """Explicitly retire one stopped owned service; retain its state for rollback."""
    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", chain_id) is None:
        raise ValueError("invalid chain id for retirement")
    name = f"dbus-mqtt-chain-{chain_id}"
    owned = _registry(package_root)
    if name not in owned:
        raise ValueError("retirement only accepts installer-owned topology services")
    link = service_root / name
    if link.exists() or link.is_symlink():
        raise ValueError("stop the main/log supervisors and unlink the service before retirement")
    target = package_root / "service" / name
    if target.is_symlink() or not target.is_dir() or (target / "log").is_symlink():
        raise ValueError("owned persistent service directory is missing or symlinked")
    _require_quiescent(target)
    retired = package_root / "retired-topology"
    destination = retired / name
    if retired.is_symlink() or destination.exists() or destination.is_symlink():
        raise ValueError("retirement destination is symlinked or already contains recovery state")
    retired.mkdir(exist_ok=True)
    target.rename(destination)
    del owned[name]
    try:
        _atomic_text(
            package_root / "topology-services.json",
            json.dumps({"config_version": 1, "services": owned}, indent=2) + "\n",
            0o600,
        )
    except OSError:
        destination.rename(target)
        raise
    return destination


def require_no_owned_services(package_root: Path = Path("/data/dbus-mqtt-battery")) -> None:
    """Prevent legacy uninstall from silently abandoning topology-owned services."""
    if _registry(package_root):
        raise ValueError("explicitly retire every topology service with --retire before uninstall")


def main() -> int:
    """Print a preflight plan by default; only --apply creates launchers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument(
        "--check-live", action="store_true", help="Also inspect live D-Bus identities"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Install managed services after live preflight"
    )
    parser.add_argument(
        "--retire", metavar="CHAIN_ID", help="Retire one stopped, unlinked owned service"
    )
    parser.add_argument(
        "--check-uninstall",
        action="store_true",
        help="Refuse uninstall while topology services remain",
    )
    args = parser.parse_args()
    try:
        if args.retire or args.check_uninstall:
            if (
                args.manifest
                or args.apply
                or args.check_live
                or (args.retire and args.check_uninstall)
            ):
                raise ValueError("retirement/uninstall check must be used alone")
            if args.retire:
                print(retire_service(args.retire))
            else:
                require_no_owned_services()
            return 0
        if args.manifest is None:
            raise ValueError("a manifest is required for preflight/apply")
        identities = live_battery_identities() if args.check_live or args.apply else None
        plan = build_plan(args.manifest, live_identities=identities)
        if args.apply:
            apply_plan(plan)
        print(json.dumps(plan.summary(), indent=2))
    except (OSError, ValueError, ImportError, RuntimeError) as error:
        parser.exit(1, f"Topology installation refused: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
