"""Execute SetupHelper dispatch with mapped paths and stable supervisor state."""

import os
import re
import subprocess
import sys
from pathlib import Path


def test_setup_preserves_supervisor_state_and_records_completion(tmp_path):
    """Setup keeps configured chains and finalizes the installed version."""
    repo = Path(__file__).resolve().parents[1]
    package = "dbus-mqtt-battery"
    source = tmp_path / "source"
    source.mkdir()
    installer = source / "setup"
    installer.write_text(
        re.sub(
            r"(?<![\w/}])(/data|/service|/var/log)",
            lambda match: str(tmp_path) + match.group(),
            (repo / "setup").read_text(),
        )
    )
    helpers = tmp_path / "data/SetupHelper/HelperResources"
    helpers.mkdir(parents=True)
    options = tmp_path / "data/setupOptions" / package
    options.mkdir(parents=True)
    for key, value in {"chains": "3", "batteries": "4", "cellsPerBms": "4"}.items():
        (options / key).write_text(value + "\n")
    (source / "version").write_text((repo / "version").read_text())
    (source / "gitHubInfo").write_text((repo / "gitHubInfo").read_text())
    (helpers / "IncludeHelpers").write_text(
        'scriptAction=INSTALL\nscriptDir="' + str(source) + '"\n'
        'setupOptionsDir="' + str(options) + '"\n'
        'endScript() { cp "$scriptDir/version" "$TEST_ROOT/completed-version"; exit 0; }\n'
    )
    service_root = tmp_path / "service"
    service_root.mkdir()
    tracked = []
    for name in ["dbus-mqtt-chain1", "dbus-mqtt-chain2", "dbus-mqtt-chain3"]:
        persistent = tmp_path / "data" / package / "service" / name
        for directory in (persistent / "supervise", persistent / "log/supervise"):
            directory.mkdir(parents=True)
            (directory / "lock").write_text("owned-by-supervisor")
            os.mkfifo(directory / "ok")
            tracked.extend((directory, directory / "lock"))
        tracked.extend((persistent, persistent / "log"))
        (service_root / name).symlink_to(persistent)
    inodes = [path.stat().st_ino for path in tracked]
    rc = tmp_path / "data/rc.local"
    rc.write_text("#!/bin/sh\necho existing-task\nexit 0\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    svc = bin_dir / "svc"
    svc.write_text("#!/bin/sh\nexit 0\n")
    svc.chmod(0o755)
    environment = dict(
        os.environ, PATH=str(bin_dir) + os.pathsep + os.environ["PATH"], TEST_ROOT=str(tmp_path)
    )
    for _ in range(2):
        result = subprocess.run(
            ["bash", str(installer)], env=environment, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr
        assert [path.stat().st_ino for path in tracked] == inodes
        assert (tmp_path / "completed-version").read_text() == (repo / "version").read_text()
    boot = str(tmp_path / "data" / package / "boot.sh")
    assert rc.read_text().splitlines().count(boot) == 1
    assert rc.read_text().index(boot) < rc.read_text().index("exit 0")
    assert "echo existing-task" in rc.read_text()
    for name in ["dbus-mqtt-chain1", "dbus-mqtt-chain2", "dbus-mqtt-chain3"]:
        run = (service_root / name / "run").read_text()
        assert "exec 2>&1" in run
        assert '--cells-per-bms "4"' in run
        assert "s25000 n4" in (service_root / name / "log/run").read_text()
