"""Validate the shipped archive rather than importing the source checkout."""

import hashlib
import subprocess
import sys
import tarfile
import tomllib
from pathlib import Path

import pytest

from scripts.build_release import build_archive

ROOT = Path(__file__).resolve().parents[1]
TAG = (ROOT / "version").read_text().strip()


def test_archive_contains_importable_runtime(tmp_path):
    """Require reproducible, version-consistent files and a working extracted import."""
    archive = build_archive(ROOT, TAG, tmp_path)
    checksum = archive.with_suffix(".gz.sha256").read_text().split()[0]
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == checksum
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "version"
    ] == TAG.removeprefix("v")
    first_bytes = archive.read_bytes()
    assert build_archive(ROOT, TAG, tmp_path).read_bytes() == first_bytes
    with tarfile.open(archive) as package:
        assert package.getmember("dbus-mqtt-battery/setup").mode == 0o755
        package.extractall(tmp_path / "extracted", filter="data")
    root = tmp_path / "extracted/dbus-mqtt-battery"
    assert (root / "version").read_text().strip() == TAG
    subprocess.run(["bash", "-n", str(root / "setup")], check=True)
    # Only device-specific libraries are mocked. The package, DVCC module,
    # entrypoint, paho client and version all come from the actual archive.
    smoke = """
import runpy
import sys
import types
for name in ("dbus", "dbus.mainloop", "dbus.mainloop.glib", "gi", "gi.repository", "vedbus"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["dbus.mainloop.glib"].DBusGMainLoop = lambda **kwargs: None
sys.modules["gi.repository"].GLib = types.ModuleType("GLib")
sys.modules["vedbus"].VeDbusService = type("VeDbusService", (), {})
sys.path.insert(0, sys.argv[1])
entry = runpy.run_path(sys.argv[1] + "/dbus-mqtt-battery.py", run_name="package_smoke")
assert entry["VERSION"] == sys.argv[2]
assert entry["create_poll_function"].__module__ == "dbus_mqtt_battery.dbus_utils"
assert "dbus_shared" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-I", "-c", smoke, str(root), TAG.removeprefix("v")],
        cwd=tmp_path,
        check=True,
    )


def test_release_tag_must_match_version(tmp_path):
    """Reject accidental publication under a different version tag."""
    with pytest.raises(ValueError, match="committed version"):
        build_archive(ROOT, "v0.0.0", tmp_path)
