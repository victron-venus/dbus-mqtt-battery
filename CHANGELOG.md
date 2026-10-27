# Changelog

## [2.7.13] - Unreleased

### Changed

- Update the packaging toolchain to setuptools 84.2.1 and the development linter to Ruff 0.17.2 with matching locked dependencies.
- Refresh grouped shared CI references and keep RC workflow, runtime, and policy pins aligned.
- Upload release assets using the verified draft ID and tolerate bounded GitHub metadata propagation delays.

### Upgrade

This maintenance release changes packaging and release tooling. Application logic, the MQTT protocol, and the paho-mqtt runtime dependency remain unchanged. Preserve an accepted package for rollback.

### Security

The setuptools build requirement is now 84.2.1. This is a build-tool dependency update, not a change to runtime authentication or transport. Continue restricting broker access to trusted producers.

## [2.7.12] - Unreleased

### Changed

- Update the locked Ruff toolchain to 0.17.1.
- Align the local validation installer with the committed dependency lock and hash-locked security tools.
- Refresh pinned shared CI workflows and the patched Scorecard runtime.

### Upgrade

Use the existing upgrade procedure and preserve configuration and data. This
maintenance release does not change MQTT messages or require a data migration.

### Security

Use an authenticated broker and restrict which producers can publish battery
telemetry. Keep the documented network restrictions in place; the tooling
updates do not add authentication to incoming measurements. Existing
dependency and secret scans remain required before publication.

## [2.7.11] - 2026-10-18

### Changed

- Route Dependabot metadata approval through the trusted default branch while preserving the existing author and review policy.
- Refresh pinned shared CI workflows and their source-bound release validation.

### Upgrade

Use the documented upgrade procedure and preserve local configuration and data. No configuration migration is required. Keep the previous immutable release available for rollback.

### Security

This maintenance release preserves the existing security and release-validation requirements. Publication does not deploy the service or establish physical-device acceptance.

## [2.7.10] - 2026-10-17

- Keep the local package version in `uv.lock` synchronized through the release policy; dependency versions and hashes remain unchanged.
- Add an opt-in local-only MQTT destination guard and document authenticated producer/broker boundaries.
- Publish project governance, community reporting contact and assurance roadmap.

### Upgrade

Existing remote MQTT settings continue to work. Arrange a protected local broker before enabling `local_only = true` or `--local-only`.

### Security

The guard prevents accidental remote plaintext connections when selected; it does not authenticate local users or MQTT publishers. No hardware behavior or deployed broker policy is changed.

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.7.9] - 2026-10-14

### Changed

- Validate each public release candidate with bounded, network-isolated fuzz campaigns against its exact source revision.
- Require positive engine execution counts, instrumented production code and validated seed corpora; retain source-bound reports and diagnostic evidence.
- Run deterministic seed and configuration checks in ordinary CI without starting mutation campaigns on betas.

### Upgrade

Use the existing installation procedure, preserve local configuration and keep the previous accepted package for rollback. This maintenance release updates release validation and does not change the application configuration format. Deployment and applicable device or integration acceptance remain separate from publication.

### Security

RC publication rejects absent or invalid source-bound fuzz reports. The fuzz runner bounds corpus paths, file types and size. No application vulnerability or new target-device acceptance is claimed by these tooling changes.

## [2.7.8] - Development line

### Release candidate validation

- Require bounded fuzzing on public release candidates, with source-bound reports and retained diagnostics; beta and ordinary CI do not run mutation campaigns.
- Reject oversized numeric telemetry values with the normal validation error.

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
