# dbus-mqtt-battery

[![CI](https://github.com/victron-venus/dbus-mqtt-battery/actions/workflows/ci.yml/badge.svg)](https://github.com/victron-venus/dbus-mqtt-battery/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Release](https://img.shields.io/github/v/release/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/releases)
[![Downloads](https://img.shields.io/github/downloads/victron-venus/dbus-mqtt-battery/total)](https://github.com/victron-venus/dbus-mqtt-battery/releases)
[![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Venus OS](https://img.shields.io/badge/Venus%20OS-3.x-blue)](https://github.com/victronenergy/venus)
[![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)](https://github.com/victron-venus/dbus-mqtt-battery)
[![GitHub watchers](https://img.shields.io/github/watchers/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/watchers)
[![GitHub contributors](https://img.shields.io/github/contributors/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/graphs/contributors)
[![GitHub issues](https://img.shields.io/github/issues/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venues/dbus-mqtt-battery/issues)
[![GitHub closed issues](https://img.shields.io/github/issues-closed/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/issues?q=is%3Aissue+is%3Aclosed)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/pulls)
[![GitHub last commit](https://img.shields.io/github/last-commit/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/commits/main)
[![Code size](https://img.shields.io/github/languages/code-size/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery)
[![Repo size](https://img.shields.io/github/repo-size/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery)
[![Maintenance](https://img.shields.io/badge/Maintained%3F-yes-green.svg)](https://github.com/victron-venus/dbus-mqtt-battery/graphs/commit-activity)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/victron-venus/dbus-mqtt-battery/pulls)
[![Made with Python](https://img.shields.io/badge/Made%20with-Python-1f425f.svg)](https://www.python.org/)
[![Victron Community](https://img.shields.io/badge/Victron-Community-blue)](https://community.victronenergy.com/)

---

<!-- ci-release-process:start -->
## Release process

See the [release strategy](RELEASING.md) for validation, nightly, beta, RC and stable promotion rules, and the [operator runbook](docs/release-workflow.md) for local commands.
<!-- ci-release-process:end -->

## Runtime archive

Release archives contain the entrypoint, runtime package, DVCC module, SetupHelper script, and version metadata, with a separate SHA256 checksum. No sibling `dbus_shared` checkout is required. Venus OS platform libraries and the declared `paho-mqtt` dependency must be available on the device.

---

## Aggregate telemetry freshness

Each ESP32 aggregate reading (voltage, current, power, state of charge, and capacity) expires independently after 60 seconds. If one aggregate topic stops updating, the service falls back to current per-BMS readings for that field; updates on other topics cannot keep the old value active. Fresh zero readings remain valid. Invalid or non-finite aggregate payloads do not refresh telemetry.

Each configured series BMS must also supply fresh voltage and keep every live measurement it has published (current, power, SoC, cell voltages and temperatures) within the same 60-second window. Status messages, capacity and cycle counters cannot refresh these measurements. Optional sensors that have never been published remain optional; binary charge/discharge flags stay latched while telemetry is fresh because they may only be published on change.

At startup, or when any configured BMS is missing, explicitly offline or stale, the chain reports disconnected with communication/internal-failure alarms, clears its aggregate electrical readings and publishes zero charge/discharge permissions and current limits. A missing series module cannot disappear from the safety calculation. Operation resumes when the entire configured chain is fresh again, while preserving BMS charge/discharge blocks. Check the configured battery count and publish intervals if the chain remains unavailable.

## Local development

The service runtime retains Python 3.7+ support. Development and release tooling
use Python 3.11, matching CI; `.python-version` selects it for uv. The release
packaging tests use the Python 3.11 standard-library `tomllib` module.

```sh
uv sync --locked --extra test
uv run --locked --extra test python3 -m pytest tests -q
uv build
```

`uv.lock` records the runtime dependency and development/test extras, including
compatible dependency versions for older service interpreters. Device installation
continues to use SetupHelper; the development Python selection does not change it.

## Completed Features

- ✅ **Release packaging**: Candidate artifacts and checksums; see the [release strategy](RELEASING.md).

---

### Configuration Options

| Option | File | Default | Description |
|--------|------|---------|-------------|
| Chains | `chains` | `2` | Number of battery chains (1-10) |
| Batteries | `batteries` | `4` | Batteries per chain |
| Cells/BMS | `cellsPerBms` | `4` | Cells per BMS module (4 for 12V LiFePO4) |

**Notes:**
- Chain services are auto-discovered from ALL battery services on D-Bus (not just `mqtt_chain*`)

**Examples:**
- 1 chain, 4 batteries: `chains=1`
- 2 chains, 4 batteries each: `chains=2`, `batteries=4`
- 5 chains, 8 batteries each: `chains=5`, `batteries=8`

### How PackageManager Works

PackageManager discovers packages by scanning `/data/` for directories containing both a `version` file and a `setup` script. The `setup` script (sourced from this repo) is executed with the `INSTALL` action by SetupHelper, which:

- Creates chain services (`dbus-mqtt-chain1`, `dbus-mqtt-chain2`, etc.) based on configuration
- Copies Python scripts to `/data/dbus-mqtt-battery/`

The shipped `gitHubInfo` file tracks the `main` branch for PackageManager updates:
```
victron-venus:main
```
For the v2.7.5 package, use the release archive in the CLI instructions below. The historical `latest` Git tag is not the latest GitHub release.

### Uninstall

Via PackageManager: Settings → PackageManager → dbus-mqtt-battery → Uninstall

Via CLI:
```bash
ssh Cerbo '/data/dbus-mqtt-battery/setup uninstall'
```

### Option 2: CLI Install (for GUI v2 users)

If you're using GUI v2 (where PackageManager menu is not available), install the v2.7.5 archive via SSH. It extracts directly into `dbus-mqtt-battery/`. Back up the existing installation and settings first. Stop the chain processes before extracting over the existing directory; retain the directory itself and its supervisor state:

```bash
ssh Cerbo

# Download and install
cd /data
for service in /service/dbus-mqtt-chain*; do
    [ ! -d "$service" ] || svc -d "$service"
done
# Confirm the existing chain processes are down before continuing.
svstat /service/dbus-mqtt-chain* 2>/dev/null || true
wget -qO - https://github.com/victron-venus/dbus-mqtt-battery/releases/download/v2.7.5/dbus-mqtt-battery-v2.7.5.tar.gz | tar -xzf -
chmod +x /data/dbus-mqtt-battery/setup

# Configure (optional, before install)
mkdir -p /data/setupOptions/dbus-mqtt-battery
echo "2" > /data/setupOptions/dbus-mqtt-battery/chains           # Number of chains
echo "4" > /data/setupOptions/dbus-mqtt-battery/batteries        # Batteries per chain

# Install
/data/dbus-mqtt-battery/setup install
for service in /service/dbus-mqtt-chain*; do
    [ ! -d "$service" ] || svc -u "$service/log" "$service"
done

# Update: select the desired version from GitHub Releases and use its archive URL
# Uninstall
/data/dbus-mqtt-battery/setup uninstall
```

### Option 3: Deploy Script (from local machine)

```bash
cd ~/victron/dbus-mqtt-battery
chmod +x deploy.sh
./deploy.sh
```

This downloads the latest version from GitHub and runs `setup install`.

## Documentation

- [System Architecture](./.github/docs/system-architecture.md) - Data flow diagrams, runbook

## Related Projects

This project is part of the Victron Venus OS integration suite:

| Project | Description |
|---------|-------------|
| [inverter-control](https://github.com/victron-venus/inverter-control) | Advanced ESS external control system with grid-zero targeting |
| [inverter-dashboard](https://github.com/victron-venus/inverter-dashboard) | Real-time web dashboard (Python/FastAPI) via MQTT |
| [inverter-dashboard-go](https://github.com/victron-venus/inverter-dashboard-go) | High-performance Go rewrite of the web dashboard |
| [inverter-desktop](https://github.com/victron-venus/inverter-desktop) | Native desktop application (Rust/Tauri) for system monitoring |
| **dbus-mqtt-battery** (this) | MQTT to D-Bus bridge for JBD BMS battery integration |
| [dbus-tasmota-pv](https://github.com/victron-venus/dbus-tasmota-pv) | Tasmota smart plug integration as a PV inverter on D-Bus |
| [esphome-jbd-bms-mqtt](https://github.com/victron-venus/esphome-jbd-bms-mqtt) | ESP32 Bluetooth monitor for JBD BMS batteries |
| [inverter-monitoring](https://github.com/victron-venus/inverter-monitoring) | TIG (Telegraf, InfluxDB, Grafana) monitoring stack |
| [terraform-github-victron](https://github.com/victron-venus/terraform-github-victron) | Infrastructure as Code for the GitHub organization |

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature-name`)
3. Commit your changes
4. Push to the branch (`git push origin feature-name`)
5. Create a Pull Request

## Support

For issues specific to:
- **MQTT bridge**: Check connection to Venus OS MQTT broker
- **D-Bus integration**: Verify D-Bus service registration
- **JBD BMS**: Confirm ESP32 publishing data to MQTT
- **This project**: Open an issue in this repository

**Note:** This is a community project and is not affiliated with Victron Energy.

## Venus OS runtime and installation notes

A series string is available only while every configured BMS reports a fresh,
finite voltage and remains online. Availability or SoC messages cannot keep an
old voltage fresh. If any module disappears, the aggregate marks `/Connected=0`,
invalidates DC measurements and SoC, and sets charge/discharge permissions and
DVCC current limits to zero until the complete string recovers. Configure the
actual number of BMS units; an overstated count now correctly remains offline.

Low-SoC alarms still update each poll. Logs record alarm transitions and one
reminder every two minutes, avoiding the previous warning every two seconds.

SetupHelper uses the configured `cellsPerBms` in each runner. Persistent runners
live under `/data`; `boot.sh` restores every installed chain, including chains
above two, before an existing `exit 0` in `/data/rc.local`. Native `multilog` keeps
four 25 KB rotated files plus the current file per chain. The release includes
its runtime helpers; no separate `dbus_shared` package is required. SetupHelper
completion records the installed version for PackageManager.

Hardware-free tests cover complete-series availability, stale voltage, invalid
DVCC output, alarm log throttling, and existing calculation/control behavior.
They do not exercise physical batteries or charger responses.
