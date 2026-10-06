# dbus-mqtt-battery

[![CI](https://github.com/victron-venus/dbus-mqtt-battery/actions/workflows/ci.yml/badge.svg)](https://github.com/victron-venus/dbus-mqtt-battery/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Release](https://img.shields.io/github/v/release/victron-venus/dbus-mqtt-battery)](https://github.com/victron-venus/dbus-mqtt-battery/releases)
[![Downloads](https://img.shields.io/github/downloads/victron-venus/dbus-mqtt-battery/total)](https://github.com/victron-venus/dbus-mqtt-battery/releases)
[![Python 3.12.x](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
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

## Python runtime

Native Venus OS packages target **Python 3.12.x**. The audited Cerbo on Venus OS
v3.75 reports Python **3.12.13**; the [official Venus OS v3.79 manifest](https://updates.victronenergy.com/feeds/venus/release/sdk/venus-scarthgap-x86_64-arm-cortexa8hf-neon-toolchain-v3.79.target.manifest)
also ships 3.12.13. Local development and CI use `.python-version` / Python
3.12.13. Package metadata accepts 3.12 patch updates and rejects other minor
versions until they have been validated. Use the firmware's system interpreter
and its matching D-Bus/GI libraries on the device; do not replace the OS Python.

<!-- ci-release-process:start -->
## Release process

See the [release strategy](RELEASING.md) for validation, nightly, beta, RC and stable promotion rules, and the [operator runbook](docs/release-workflow.md) for local commands.
<!-- ci-release-process:end -->

## Runtime archive

Release archives contain the entrypoint, runtime package, DVCC module, SetupHelper script, and version metadata, with a separate SHA256 checksum. No sibling `dbus_shared` checkout is required. Venus OS platform libraries and the declared `paho-mqtt` dependency must be available on the device.

---

## Aggregate telemetry freshness

Each ESP32 aggregate reading (voltage, current, power, state of charge, and capacity) expires independently after 60 seconds. If one aggregate topic stops updating, the service falls back to current per-BMS readings for that field; updates on other topics cannot keep the old value active. Fresh zero readings remain valid. Invalid or non-finite aggregate payloads do not refresh telemetry.

Each configured series BMS must supply fresh voltage, current, SoC, all configured cell voltages, temperature and explicit permissions. Every required physical field expires independently within the same 60-second window. Atomic MOS status also expires independently; legacy binary permissions remain latched while measurements are fresh because old firmware publishes them only on change. Status, capacity and cycle counters cannot refresh electrical measurements.

At startup the chain reports disconnected with unknown readings and zero permissions/current limits until a complete physical frame arrives. A brief missing poll can use previously measured fields only until their original expiry. Invalid observed values and expired required fields fail closed; a missing series module cannot disappear from the safety calculation. See the readiness contract below.

## Local development

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

A series string is available only while every configured BMS reports fresh
voltage, current, SoC, temperature, every configured cell voltage, and explicit
charge/discharge permissions. Availability or SoC messages cannot keep an
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

## Temperature sensor IDs (`temps_per_bms`)

Global temperature IDs use `(battery_id - 1) * temps_per_bms + sensor_index` with a **configured** stride (default `2`). Set `temps_per_bms` in config or `--temps-per-bms` to at least the densest BMS temperature sensor index. An undersized stride does **not** renumber sensors; temperature IDs are withheld while min/max temperature **values** still use every sensor.

## Black start and telemetry readiness

The default `atomic` telemetry mode requires the matching ESPHome firmware's
non-retained `<topic-prefix>/telemetry` JSON messages. Update the bridge and both
ESP monitors as one coordinated change. Old individual topics alone will leave
the default bridge initializing with charge/discharge blocked; this is intentional.
Prepare and validate both firmware images before changing a running bridge.
Solar chargers configured to require a BMS can also stop charging while no ready
BMS is advertised. Initial charging after removal of the old BMS is not evidence
that charging will continue throughout a long firmware upgrade.

There are five `/Info/TelemetryState` values:

- `INITIALIZING`: no complete trustworthy snapshot has arrived in this process.
  Measurements are invalid (`None`), permissions and CCL/DCL are zero. CVL stays
  invalid, so the service does not advertise itself as a ready controlling BMS.
  CommunicationError is a warning; missing data is not a measured LowSoc alarm.
- `READY`: every configured BMS is complete and fresh, and neither current limit
  is zero. A complete snapshot already received before D-Bus registration is
  published immediately, without the former temporary DCL=0 state.
- `PROTECTION`: measurements are valid, but a real BMS veto or calculated
  voltage/temperature limit blocks charge or discharge. These blocks are never
  bypassed by startup timing or a communications grace period.
- `DEGRADED`: a poll or transport connection is missing, but every required
  physical field is still within its original 60-second deadline. Missing fields
  and permissions are never refreshed by receipt of unrelated data. Newly seen
  low cells, temperatures or MOS vetoes still tighten the actual DVCC limits;
  partial frames cannot raise a previously published current limit or clear a
  veto. A complete frame is required before starting from a cold process.
- `STALE`: a previously ready process lost required telemetry. Measurements are
  invalid, CCL/DCL and permissions are zero, and the last CVL remains advertised
  so a previously selected controlling BMS still enforces the zero current limits.

Calculated protective current reductions take effect immediately, including
nonzero cell/temperature derating and hard zero limits. Only increases use the
existing gradual CCL ramp, measured with a monotonic clock.

The complete measurement/permission update uses one D-Bus `ItemsChanged` batch.
`/Info/DataComplete`, `/Info/MissingData`, `/Info/TelemetryPartial`, and
`/Info/TelemetrySource` explain
readiness. `/Info/DataAge` measures the oldest required physical sample;
`/Info/DataTimeout` is 60 seconds. `/Info/LastMeasurementMonotonic` is its timestamp
in the **Cerbo host's monotonic clock**, obtained by subtracting the producer's
sample age from local receipt time. Another service on the same Cerbo can expire
it even if this publisher stops updating. Do not compare it with ESP uptime or
with timestamps from another host.

The atomic payload has `schema: 1`, a nonempty string `boot_id`, an increasing
integer `seq`, boolean `ready`, and a `batteries` array. Each configured entry
contains `id`, `age_ms`, `voltage`, `current`, `soc`, `temperature`, `cells` (all
configured voltages), and boolean `charging`, `discharging`, `online`. Optional
`temperatures` preserves individual sensor extrema; `capacity` is remaining Ah
and `cycles` is charge-cycle count. Zero measured current/SoC and false MOS
permissions are valid; missing values never become zero or implicit permission.
The producer must freeze these fields from one completed physical polling round.
Malformed frames, explicitly invalid observations and expired required fields
invalidate the string. Partial rounds can preserve unexpired measurements only
when they provide per-entry `seen_mask`, `valid_mask` and `observed_age_ms`.
Bits 0–8 are voltage, current, SoC, temperature, four cell voltages and MOS status.
An observed invalid bit fails closed; an absent bit preserves its previous value
and timestamp. Unknown MOS status is `null`, not a false protection or true grant.
`online: false` with this metadata means an incomplete current poll; it does not
erase valid historical physical evidence. Incomplete frames without this metadata
still fail closed. The masks describe four-cell BMS modules.

Older/duplicate sequence numbers and retired boot sessions cannot renew freshness.
A process retains up to 4096 retired producer-session IDs without evicting any.
If that bound is reached, a different producer boot is rejected with an explicit
restart-required diagnostic. Existing measurements and vetoes keep their original
expiry, and the currently accepted boot may still publish newer frames. Restarting
the driver admits a new boot but starts the documented new-process trust boundary;
it deliberately does not preserve replay history across process restarts.
A brief MQTT disconnect, producer offline notification or new producer boot keeps
only unexpired prior physical evidence. After these events, no timestamp is
renewed until a complete new frame arrives; no permission is inferred from an
online notification. Retained messages are ignored. Once atomic messages are
used, individual legacy topics cannot restore availability after an atomic fault.

For deliberate compatibility only, set `[mqtt] telemetry_mode = legacy`, pass
`--telemetry-mode legacy`, or put `legacy` in
`/data/setupOptions/dbus-mqtt-battery/telemetryMode` before running SetupHelper.
The default for that setup option is `atomic`; setup preserves the existing
option files and configured chain counts. Legacy mode still requires the full
set of live measurements and explicitly received permissions. Because old
binary sensor topics are change-driven, permissions remain latched while all
measurements stay fresh, but are discarded on broker disconnect. Some old
firmware cannot republish unchanged permissions and will remain unavailable.
Legacy messages have no physical-frame identity: a non-retained replay of a
cached sensor value cannot be distinguished from a new reading. It is not a
substitute for atomic firmware during black start.

These changes do not supply power to a Cerbo connected to the inverter's AC
output. Independent protected power for the Cerbo, ESP monitors and necessary
network equipment is required to break that power/recovery dependency. A Multi
already configured to require a BMS may still stop while no BMS is ready; delaying
CVL advertisement is not a guarantee that such a Multi can black-start. Likewise,
process restart loses the in-memory `READY`/`STALE` and sequence history and starts
initializing again. No saved measurement is replayed as a fresh safety permission.
Sample age is measured at the producer's publication; arbitrary network buffering
time is not observable without an additional clock or request/response protocol.
