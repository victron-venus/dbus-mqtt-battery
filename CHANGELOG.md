# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.7.8] - Development line

### Release overview

Aggregates JBD BMS MQTT telemetry and publishes a Victron D-Bus battery service. The existing README documents configuration and external interfaces for this development line.

### Maintenance

- Record test and build dependencies in `uv.lock` for reproducible Python checks, preserving the existing locked runtime versions.
- Separate command-line overrides and legacy MQTT decoding into focused helpers while preserving permission, freshness and atomic-frame handling.
- Publish reviewed release notes from the exact source commit used to build each candidate, preserving build provenance.
- Document contribution checks, confidential security reporting and the project-specific trust boundaries.
- Require complete Bandit scans with no unresolved findings; reject malformed or incomplete scanner output. Document narrowly reviewed tooling and synthetic-fixture exceptions.
- Reject incomplete aggregate battery snapshots with runtime checks that remain active under Python optimization.

### Upgrade

These maintenance changes do not introduce a configuration or data migration. Retain local configuration and credentials when using the documented update procedure. Validate the candidate on an isolated system before production use; automated checks do not establish hardware acceptance.

### Security

Private vulnerability reporting and response policy are documented in SECURITY.md. This maintenance update strengthens release evidence and review instructions; it does not replace deployment authentication, network isolation or independent equipment safeguards. No new project CVE is announced by these changes.

## [Unreleased]

## [2.7.5] - 2026-09-12

### Fixed
- Log persistent BMS alarm states on transitions and bounded reminders instead of every poll.
- Restore every configured MQTT chain service after reboot, including installations with more than two chains.
- Preserve persistent supervisor directories and place boot restoration before the existing `rc.local` exit.
- Finalize SetupHelper installation so PackageManager records the installed release.

## [2.7.4] - 2026-09-12

### Fixed
- Keep charge and discharge disabled while any configured series BMS is missing, offline or stale, including startup; publish zero current limits and invalidate aggregate electrical readings.
- Expire each observed live BMS measurement independently after 60 seconds using a monotonic clock. Status and static metadata cannot revive old readings.
- Preserve latched BMS charge/discharge blocks during telemetry recovery and count missing modules consistently in alarms and status.

## [2.7.3] - 2026-09-12

### Fixed
- Expire each aggregate telemetry field independently after 60 seconds; unrelated MQTT updates cannot preserve stale current or power.
- Include the runtime package and DVCC module in release archives, with SHA256 checksums and no sibling `dbus_shared` checkout required.

### Added
- Circuit breaker around the poll loop: a hung `service.update()` call is bounded by a 10s SIGALRM timeout; after 3 consecutive timeouts the breaker opens for 60s before retrying (half-open)
- `/Alarms/CommunicationError` (0=OK, 2=alarm) and `/System/StaleData` now reflect MQTT data freshness — set when no MQTT message arrives for more than `STALE_TIMEOUT` (60s) or when no battery data is valid

### Fixed
- Export missing `PATH_DC_VOLTAGE`, `PATH_DC_CURRENT`, `PATH_DC_POWER` and `load_config` from `dbus_mqtt_battery` package (`__init__`) — main script previously failed on import

## [2.7.0] - 2026-04-04

### Added
- Dynamic chain configuration via setupOptions
- Configurable number of battery chains (1-10)
- Setup script generates services dynamically

### Changed
- Services now created at install time based on configuration
- Removed static service definitions from repository
- README updated with configuration options

## [2.6.0] - 2026-04-04

### Added
- `--bms-first` argument for multi-chain setups with single ESP32
- `current_total_seen` and `soc_total_seen` flags for fallback logic
- Dependabot and CodeQL security scanning
- Secret scanning with push protection

### Fixed
- Current showing 0A when MQTT doesn't publish `current_total` topic
- Fallback to sum of individual BMS currents when aggregate unavailable
- Service run scripts now use `exec 2>&1` for proper logging to svlogd
- D-Bus registration issues with daemontools services

### Changed
- Improved aggregate data calculation with fallback mechanism
- Updated install.sh with corrected stderr redirection

## [2.5.1] - 2026-03-29

### Added
- `commit.sh` and `release.sh` helper scripts
- Additional badges in README

### Changed
- Replaced SSH host alias 'r' with 'Cerbo' in README

## [2.5.0] - 2026-03-28

### Added
- Thread-safe data access with locks
- MQTT auto-reconnect with exponential backoff
- Graceful shutdown handling (SIGTERM, SIGINT)
- Periodic garbage collection

### Changed
- Improved 24/7 reliability
- Better error handling

## [2.4.0] - 2026-03-27

### Added
- Support for multiple battery chains
- SmartShunt integration for Chain 3

### Changed
- Command-line arguments for all configuration
- Improved logging

## [2.0.0] - 2026-03-25

### Added
- Initial public release
- MQTT to D-Bus bridge for JBD BMS
- Support for 4 batteries per chain
- Cell voltage reporting
- Temperature monitoring
- Charge/discharge FET status

[2.6.0]: https://github.com/victron-venus/dbus-mqtt-battery/releases/tag/v2.6.0
[2.5.1]: https://github.com/victron-venus/dbus-mqtt-battery/releases/tag/v2.5.1
[2.5.0]: https://github.com/victron-venus/dbus-mqtt-battery/releases/tag/v2.5.0
[2.4.0]: https://github.com/victron-venus/dbus-mqtt-battery/releases/tag/v2.4.0
[2.0.0]: https://github.com/victron-venus/dbus-mqtt-battery/releases/tag/v2.0.0
