"""Exercise the deployed source-overlay script with a local GitHub-shaped archive."""

import os
import subprocess
import tarfile
from pathlib import Path


def test_source_overlay_preserves_local_configuration_and_supervisors(tmp_path):
    """An update overlays code without replacing persistent supervision state."""
    repo = Path(__file__).resolve().parents[1]
    package = repo.name
    script = (
        (repo / "deploy.sh")
        .read_text()
        .split("<<'REMOTE_STAGE'\n", 1)[1]
        .split("\nREMOTE_STAGE", 1)[0]
    )
    remote_script = tmp_path / "stage.sh"
    remote_script.write_text(script)
    source = tmp_path / (package + "-main")
    source.mkdir()
    (source / "setup").write_text("#!/bin/sh\nexit 0\n")
    (source / (package + ".py")).write_text("# Updated runtime\n")
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(source, arcname=source.name)
    installed = tmp_path / "installed"
    supervisor = installed / "service/example/supervise"
    supervisor.mkdir(parents=True)
    (supervisor / "lock").write_text("live-supervisor")
    os.mkfifo(supervisor / "ok")
    config = installed / "config.ini"
    config.write_text("preserved local settings\n")
    paths = (installed, supervisor, supervisor / "lock", supervisor / "ok")
    inodes = [path.stat().st_ino for path in paths]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wget = bin_dir / "wget"
    wget.write_text('#!/bin/sh\ncp "$TEST_ARCHIVE" "$2"\n')
    wget.chmod(0o755)
    subprocess.run(
        ["sh", str(remote_script), "victron-venus/" + package, str(installed)],
        env=dict(
            os.environ,
            PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
            TEST_ARCHIVE=str(archive),
        ),
        check=True,
    )
    assert [path.stat().st_ino for path in paths] == inodes
    assert config.read_text() == "preserved local settings\n"
    assert (installed / (package + ".py")).read_text() == "# Updated runtime\n"
