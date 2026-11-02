#!/usr/bin/env python3
"""Regenerate recorded frames from a pinned external ESPHome producer checkout.

Ordinary consumer tests read the committed fixtures and need neither this tool,
PyYAML, a compiler, nor a sibling repository. Regeneration needs PyYAML as pinned
by the producer's .github/requirements-workflow-contracts.txt.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess  # nosec B404
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
COUNTS = ((16,), (4, 8, 4), (32, 32, 32, 32), (4, 4, 4, 4))


def sha256(data: bytes) -> str:
    """Hash the exact producer input or generated fixture bytes."""
    return hashlib.sha256(data).hexdigest()


def generate(producer: Path) -> None:
    """Execute the real collector and publication lambda with a host JSON sink."""
    compiler = shutil.which("c++")
    git = shutil.which("git")
    if compiler is None or git is None:
        raise RuntimeError("Git and a C++17 compiler are required for fixture regeneration")
    # Resolved executable and fixed Git arguments; producer is an argv path, not shell text.
    commit = subprocess.check_output(  # nosec B603
        [git, "-C", str(producer), "rev-parse", "HEAD"], text=True
    ).strip()
    paths = (
        "components/series_telemetry.h",
        "scripts/generate-series-config.py",
        "jbd-all-batteries1.yaml",
        "packages/mqtt-availability.yaml",
    )
    hashes = {}
    for path in paths:
        data = (producer / path).read_bytes()
        # Read only the fixed source paths from the explicitly selected producer checkout.
        committed = subprocess.check_output(  # nosec B603
            [git, "-C", str(producer), "show", f"{commit}:{path}"]
        )
        if data != committed:
            raise ValueError(f"producer input has uncommitted changes: {path}")
        hashes[path] = sha256(data)
    spec = importlib.util.spec_from_file_location(
        "series_generator", producer / "scripts/generate-series-config.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the producer generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixtures = []
    payload_hashes = {}
    for counts in COUNTS:
        config = module.build_config("contract-fixture", "contract/fixture", list(counts))
        publication = next(action["if"] for action in config["script"][0]["then"] if "if" in action)
        payload = publication["then"][0]["mqtt.publish_json"]["payload"]
        name = "-".join(map(str, counts))
        payload_hashes[name] = sha256(payload.encode())
        code = (HERE / "render.cpp.in").read_text().replace("@COUNTS@", ",".join(map(str, counts)))
        code = code.replace("@PAYLOAD@", payload)
        with tempfile.TemporaryDirectory(prefix="jbd-producer-contract-") as directory:
            scratch = Path(directory)
            source, binary = scratch / "render.cpp", scratch / "render"
            source.write_text(code)
            # Resolved compiler, fixed flags and private temporary output paths; no shell.
            subprocess.run(  # nosec B603
                [
                    compiler,
                    "-std=c++17",
                    "-O1",
                    "-fsanitize=address,undefined",
                    "-fno-omit-frame-pointer",
                    "-I",
                    str(producer),
                    "-I",
                    str(HERE),
                    str(source),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            # Execute only the fixture binary just compiled in the private temporary directory.
            output = subprocess.check_output([str(binary)], text=True, timeout=10)  # nosec B603
            frame = json.loads(output)
        fixtures.append({"name": name, "member_cell_counts": counts, "frame": frame})
    payload = (json.dumps(fixtures, indent=2, sort_keys=True) + "\n").encode()
    (HERE / "frames.json").write_bytes(payload)
    provenance = {
        "producer_repository": "https://github.com/victron-venus/esphome-jbd-bms-mqtt",
        "producer_commit": commit,
        "producer_source_sha256": hashes,
        "publication_lambda_sha256": payload_hashes,
        "frames_sha256": sha256(payload),
        "method": "Compile real collector and generated publication lambda using host JSON adapter",
        "compiler_flags": [
            "-std=c++17",
            "-O1",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
        ],
        "scope": "Native synthetic observations; no BLE, MQTT transport, ArduinoJson formatting or hardware qualification",
    }
    (HERE / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")


def main() -> None:
    """Require an explicitly selected, committed producer checkout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--producer", required=True, type=Path)
    args = parser.parse_args()
    generate(args.producer.resolve(strict=True))


if __name__ == "__main__":
    main()
