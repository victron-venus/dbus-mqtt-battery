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

## Release Channels & CI/CD

This repository provides automated build archives for Victron Venus OS installations. Release archives contain the entrypoint, runtime package, DVCC module, SetupHelper script, and version metadata, with a separate SHA256 checksum. No sibling `dbus_shared` checkout is required. Venus OS platform libraries and the declared `paho-mqtt` dependency must be available on the device:

- **Stable Releases**: Tagged as `vX.Y.Z` (e.g., `v1.0.0`). Contains packaged Venus OS installer tarballs (`dbus-mqtt-battery-*.tar.gz`).
- **Pre-releases**: Tagged with `-rc.N` or `-beta.N`. Automatically flagged as Pre-release on GitHub Releases to isolate driver testing on Venus OS hardware.
- **Nightly Builds**: Built daily at 02:00 UTC. Generates a fresh `dbus-mqtt-battery-nightly.tar.gz` package published to the **[Nightly Build Release](https://github.com/victron-venus/dbus-mqtt-battery/releases/tag/nightly)**.

---

## Aggregate telemetry freshness

Each ESP32 aggregate reading (voltage, current, power, state of charge, and capacity) expires independently after 60 seconds. If one aggregate topic stops updating, the service falls back to current per-BMS readings for that field; updates on other topics cannot keep the old value active. Fresh zero readings remain valid. Invalid or non-finite aggregate payloads do not refresh telemetry.

Each configured series BMS must also supply fresh voltage and keep every live measurement it has published (current, power, SoC, cell voltages and temperatures) within the same 60-second window. Status messages, capacity and cycle counters cannot refresh these measurements. Optional sensors that have never been published remain optional; binary charge/discharge flags stay latched while telemetry is fresh because they may only be published on change.

At startup, or when any configured BMS is missing, explicitly offline or stale, the chain reports disconnected with communication/internal-failure alarms, clears its aggregate electrical readings and publishes zero charge/discharge permissions and current limits. A missing series module cannot disappear from the safety calculation. Operation resumes when the entire configured chain is fresh again, while preserving BMS charge/discharge blocks. Check the configured battery count and publish intervals if the chain remains unavailable.

## Completed Features

- ✅ **CI/CD Releases & Nightly Builds**: Venus OS installer tarball packaging configured for automated releases

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
For the published v2.7.3 package, use the release archive in the CLI instructions below. The historical `latest` Git tag is not the latest GitHub release.

### Uninstall

Via PackageManager: Settings → PackageManager → dbus-mqtt-battery → Uninstall

Via CLI:
```bash
ssh Cerbo '/data/dbus-mqtt-battery/setup uninstall'
```

### Option 2: CLI Install (for GUI v2 users)

If you're using GUI v2 (where PackageManager menu is not available), install the published v2.7.3 archive via SSH. It extracts directly into `dbus-mqtt-battery/`:

```bash
ssh Cerbo

# Download and install
cd /data && rm -rf dbus-mqtt-battery
wget -qO - https://github.com/victron-venus/dbus-mqtt-battery/releases/download/v2.7.3/dbus-mqtt-battery-v2.7.3.tar.gz | tar -xzf -
chmod +x /data/dbus-mqtt-battery/setup

# Configure (optional, before install)
mkdir -p /data/setupOptions/dbus-mqtt-battery
echo "2" > /data/setupOptions/dbus-mqtt-battery/chains           # Number of chains
echo "4" > /data/setupOptions/dbus-mqtt-battery/batteries        # Batteries per chain

# Install
/data/dbus-mqtt-battery/setup install

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
