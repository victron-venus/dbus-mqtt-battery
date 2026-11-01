# Explicit series topologies

The opt-in topology mode represents each physical battery and its cells explicitly.
Existing installations without a topology file retain their current configuration,
schema-1 decoder, calculations, service names and protection behavior. “Legacy
compatibility” does not mean changing the current atomic MQTT mode to scalar
`telemetry_mode=legacy`.

## Supported configurations

- One MQTT collector per series string, with 1–4 physical members and 1–32 cells
  per member, within the current ESPHome generator's software envelope.
- Inventories such as `[4]`, `[4,4]`, `[4,4,4]`, `[4,4,4,4]`, `[16]` and `[8,8]`.
  Each number means cells inside one physical battery.
- Mixed inventories such as `[4,8,4]` are supported for offline observation.
  Managed output currently requires equal cell counts per member and an explicit,
  complete LiFePO4 protection profile.
- Multiple independent chains can have different inventories and device identities.

Multiple collectors in one string, generic parallel-bank control and other battery
chemistries are outside this release. The existing `dbus-virtual-battery` service
estimates a residual branch from SmartShunt minus measured chains; it is not
repurposed as a parallel-bank combiner.

See the [ESPHome generator and deployment documentation](https://github.com/victron-venus/esphome-jbd-bms-mqtt/blob/main/docs/virtual-series-battery.md)
for physical inventory configuration. Four-cell-only generated inventories use
schema 1; other cell counts use schema 2. Cell support is not hardware qualification
of every BMS, radio environment or series connection.

## Manifest

Use a versioned JSON file outside the installed source package, for example
`/data/setupOptions/dbus-mqtt-battery/topology.json`. Examples:

- [Single 16-cell battery, offline](../examples/topology-16s-offline.json)
- [Mixed 4/8/4-cell inventory, offline](../examples/topology-mixed-offline.json)
- [Single 16-cell battery, managed example](../examples/topology-16s-managed.example.json)

The managed example contains illustrative 100 Ah / 30 A charge / 40 A discharge
ratings and a 60 V equipment ceiling. Replace these with verified battery and
equipment values before commissioning. The example is not a recommendation for
your installation.

Every manifest declares `config_version: 1` and a nonempty `chains` array. Each
chain declares its stable `id`, `connection: series`, source `topic_prefix` and
`telemetry_schema`, ordered `members`, `service_suffix`, `device_instance`, explicit
aggregation policies, `output`, and `protection_profile`. Optional `product_name`
defaults to the chain ID. Each member declares `bms_id`, `cells` and optionally
`temperatures` (default 1).

Managed members additionally require `capacity_ah`, `max_charge_current` and
`max_discharge_current`. Managed chains require `system_voltage_limit` and every
protection-profile field. The computed charge-voltage limit must fit the declared
equipment ceiling. The lowest member/system current ceiling applies; adding
members in series does not multiply permissible current or amp-hour capacity.

Unknown fields, duplicate IDs/topics/device instances, invalid counts, non-finite
limits and inconsistent protection thresholds are rejected before installation.
`reserved_device_instances` defaults to `[514]`, reserving the existing residual
virtual battery. The examples also reserve 512 and 513 for the existing MQTT
chains. Live installation checks all visible battery identities as well.

The schema-2 decoder checks configured and reported cell counts, array lengths,
locally computed evidence masks, physical values, sample age and permissions.
Telemetry cannot resize a chain or remove a missing member. Cell and temperature
numbering comes from the configured member order and stays stable during outages.

## Validate and observe first

On a development machine, use the standalone preflight, which does not require
Venus OS D-Bus/GI libraries:

```bash
python dbus_mqtt_battery/topology_install.py \
  "$PWD/examples/topology-16s-offline.json"
```

On the Cerbo, `--validate-topology` also prints the resolved runtime configuration
without connecting to MQTT or registering a battery. After placing an offline
manifest outside the package, observe live data with:

```bash
python3 /data/dbus-mqtt-battery/dbus-mqtt-battery.py \
  --topology /data/setupOptions/dbus-mqtt-battery/topology.json \
  --chain house16 --broker 127.0.0.1 --observe-seconds 60
```

Observation must not register another battery D-Bus service or join the production
residual-battery source list. Two discoverable battery services are not an isolated
comparison: automatic selection can itself change system behavior.

New topology mode uses voltage-weighted BMS current, minimum member SOC and minimum
measured remaining Ah. These calculations are explicit and differ from some legacy
aggregates. Prefer an appropriately scoped physical shunt for authoritative current.
Current and Ah are not summed in series. A nominal 12/24/36/48 V label does not set
charging limits or establish inverter compatibility.

Incomplete schema-2 frames currently block the entire string immediately. Existing
schema-1 partial-frame grace is retained only in the original configuration path.
Zero current, zero SOC and false permissions remain valid observations. Missing
data does not become a zero measurement, and a missing member never becomes a
smaller healthy string.

## Installation preflight

The installer defaults to a read-only plan. The direct-file entry point avoids
requiring D-Bus/GI merely to validate a manifest offline:

```bash
python3 /data/dbus-mqtt-battery/dbus_mqtt_battery/topology_install.py \
  /data/setupOptions/dbus-mqtt-battery/topology.json
```

On the Cerbo, include current D-Bus identity checks:

```bash
python3 /data/dbus-mqtt-battery/dbus_mqtt_battery/topology_install.py \
  /data/setupOptions/dbus-mqtt-battery/topology.json --check-live
```

The plan lists the supervised service name, D-Bus identity, member cell inventory,
output mode, charge-voltage limit and equipment-voltage ceiling. No launchers change.
An existing numeric legacy service cannot be silently adopted or replaced. Choose
distinct identities for new commissioned chains; preserve the original 512/513/514
assignments unless carrying out a separately planned migration.

## Apply a commissioned managed configuration

Save the current release and exact configuration before maintenance. Use a pinned
release archive; the convenience `deploy.sh` follows `main` and is not a reproducible
candidate or rollback reference. Keep the topology file outside the package overlay.

Only after the manifest has `output: managed`, verified member ratings and a complete
protection profile, select it through SetupHelper:

```bash
printf '%s\n' /data/setupOptions/dbus-mqtt-battery/topology.json \
  > /data/setupOptions/dbus-mqtt-battery/topologyFile
/data/dbus-mqtt-battery/setup install
```

When `topologyFile` exists, it is authoritative for topology and device identity;
the old scalar chain-count options remain stored but are not used to construct
these services. The existing `broker` option still applies. An absent, unreadable
or invalid explicitly selected manifest is an error, not a fallback to old defaults.
Offline entries are accepted by preflight but cannot be installed as battery services.

The installer validates every chain before writing anything and uses quoted argv.
Services are named `dbus-mqtt-chain-<id>`. It preserves supervisor directories and
does not restart existing processes. New service links are activated only after
complete launchers and ownership metadata are written. A `.topology-ready` marker
prevents an incompletely created new service from being restored by the boot script.

A failed launcher-write phase restores prior files. If activation fails after a
new service link appears, completed runtime state is preserved because svscan may
already have started that service; inspect the plan and repeat preflight/apply.
Do not remove runtime directories underneath a running service.

For an existing configured service, changing a manifest does not hot-reload its
inventory. Restart that selected service during its commissioning window, then
verify its identity, complete cell count, fresh measurements, permissions, voltage
limit, current limits and recovery after a deliberately isolated test interruption.

## Retirement, uninstall and rollback

Removing a chain from JSON does not silently delete its service. The installer
refuses that change until the old service is explicitly retired. This prevents an
obsolete service from returning after reboot.

For the example `house16`, stop both supervisors, wait until they are no longer
running, and remove only the `/service` link:

```bash
svc -dx /service/dbus-mqtt-chain-house16 /service/dbus-mqtt-chain-house16/log
svstat /service/dbus-mqtt-chain-house16 /service/dbus-mqtt-chain-house16/log
# Continue after both supervisors have exited.
unlink /service/dbus-mqtt-chain-house16
python3 /data/dbus-mqtt-battery/dbus_mqtt_battery/topology_install.py --retire house16
```

Retirement accepts only recorded topology-owned services. It checks stopped child
and supervisor state, preserves the directory under `retired-topology/`, and removes
its ownership entry. The retained state is rollback material; remove it only after
confirming it has no remaining recovery or process dependency. Package uninstall
refuses while topology-owned services remain. Legacy uninstall behavior is unchanged
when there is no topology ownership registry.

Rollback restores the previous pinned package, exact configuration and service
selection. Restoring only an old driver is insufficient when a producer is still
publishing schema 2: restore a compatible producer/configuration as well. Before
returning to scalar SetupHelper options, explicitly retire new topology services;
removing `topologyFile` alone does not retire them.

## Troubleshooting

- **Unsupported schema or count mismatch:** align the configured inventory with the
  generated ESPHome project. Do not reduce expected counts to hide an offline member.
- **Reserved or live identity conflict:** use a genuinely free device instance and
  suffix. MQTT chain 3's historical automatic number 514 collides with the residual
  battery's default; topology mode detects that conflict.
- **Incomplete/stale data:** verify every configured member, all cells, temperature,
  MOS permissions, collector uptime and MQTT freshness. Reconnection does not make
  an old physical sample fresh.
- **Managed profile rejected:** supply every profile field, ordered limits, member
  current ratings and the equipment-voltage ceiling. Mixed inventories remain offline.
- **Configuration changed after preflight:** generate a new plan from the final file.
- **Retirement refused:** ensure the main child, logger and both supervisors have
  exited, the `/service` link is removed, and no unrelated path was substituted.

Protocol tests, legacy output comparisons, installer interruption checks and live
observation are complementary. None alone qualifies arbitrary battery wiring or
manufacturer limits. Victron's [DVCC documentation](https://www.victronenergy.com/media/pg/Cerbo_GX/en/dvcc---distributed-voltage-and-current-control.html)
describes how battery limits control the connected equipment.
