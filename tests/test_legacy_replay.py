"""Every legacy publication remains equivalent to the pre-topology runtime."""

import json
import subprocess  # nosec B404
import sys
from pathlib import Path


def test_full_legacy_publication_replay_matches_frozen_pre_topology_outputs():
    """Compare all paths and transaction batches across sixteen runtime variants."""
    root = Path(__file__).resolve().parents[1]
    expected = json.loads((root / "tests/fixtures/legacy_replay/golden.json").read_text())
    # Run the fixed repository harness with this interpreter; explicit argv, never a shell.
    result = subprocess.run(  # nosec B603
        [sys.executable, str(root / "tests/legacy_replay.py"), "--source", str(root), "--summary"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    actual = json.loads(result.stdout)
    assert actual["excluded_paths"] == expected["expected"]["excluded_paths"]
    for name, phases in expected["expected"]["scenarios"].items():
        assert actual["scenarios"][name] == phases, f"Legacy D-Bus behavior changed: {name}"
    assert actual["scenarios"].keys() == expected["expected"]["scenarios"].keys()
