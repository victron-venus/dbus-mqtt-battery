# Architecture, assurance evidence and roadmap

## Start and interfaces

[README](../README.md) describes installation, the MQTT topics, configuration,
D-Bus outputs and operation. [CONTRIBUTING](../CONTRIBUTING.md) gives development
and build commands. [Release strategy](../RELEASING.md) separates automated
qualification from actual target acceptance. Use the [protected local broker
profile](secure-local-broker.md) when authenticated publishers and local host
access are the deployment trust boundary.

`config.py` reads INI and command-line settings; explicit CLI settings override
the file. `mqtt_client.py` owns connection/reconnection and sample aggregation.
`telemetry.py` decodes bounded atomic sequence/boot/snapshot messages, while
`bms_data.py` tracks required fields and stale data. `dbus-mqtt-battery.py` maps
validated observations to the Venus service, and `dvcc.py` owns DVCC limits.
`dbus_utils.py` provides shared D-Bus metadata and IDs. QML pages expose the
battery values. The host's D-Bus libraries, broker and producer firmware remain
separate dependencies.

## Security and quality

Incomplete, expired or incompatible readings withhold valid charging evidence;
software limits do not supersede physical BMS cutoffs, fuses and contactors.
The local-only guard rejects remote destinations before MQTT initialization.
Tests cover telemetry state transitions, stale samples, atomic updates, config
precedence and D-Bus export through controlled platform fixtures. They do not
assert physical-device operation. RC fuzzing mutates the actual telemetry
parser with source-bound reports; see [fuzzing](fuzzing.md).

Python dependencies are in `pyproject.toml` and `uv.lock`; the platform supplies
D-Bus/GObject and its service manager. Ruff formatting/lint, mypy, pytest,
CodeQL, Bandit and dependency scans provide complementary checks. New major
functionality needs automated tests, fixed bugs need regressions and interface
changes need documentation and release notes. Review third-party notices in the
root and overlay directories before redistributing.

## Achievements and remaining work

[CHANGELOG](../CHANGELOG.md) records released behavior and upgrade impact.
Atomic telemetry, stable temperature IDs, package verification and the local
broker guard have executable regressions. Next priorities are real broker
integration tests, fault injection around D-Bus restart and durable history of
target-device acceptance. Measure complete maintained-source coverage, including
the executable, release helpers, shell and QML; package-only coverage is not a
whole-project 80% claim.

There is no backup maintainer or established one-week recovery arrangement;
[governance](../GOVERNANCE.md) records the gap. Complete accessibility, systematic
input validation, bit-for-bit independent builds and every-release signing are
not asserted without evidence. The [official assessment evidence](openssf-evidence.md)
is a self-assessment of practices, not a safety certification.
