# Frozen legacy publication contract

`golden.json` records the complete D-Bus publication hashes captured from the
pre-topology main source at the commit identified in that file. The capture also
matched the actual Cerbo installation reporting `v2.7.8-beta.1` before deployment.
Every runtime source file is hashed in the baseline provenance.

The replay matrix contains 16 variants: 1–4 four-cell members, first physical BMS
ID 1 or 5, and atomic or legacy MQTT input. It captures 320 states and 320 D-Bus
publication transactions, comparing 36,800 path values in each category.
Transitions cover initial registration, retained and partial startup data,
healthy measurements, real zero values, MOS veto, missing-field expiry,
disconnect/reconnect, retained packets after reconnect, producer reboot,
retired-session replay, invalid measurements and recovery. Session/replay cases
apply to atomic input; legacy cases additionally cover explicit zero aggregate
topics.

Only `/FirmwareVersion`, `/Mgmt/ProcessName` and `/Mgmt/ProcessVersion` are
excluded. Measurements, connection/diagnostic status, timestamps, cell IDs,
alarms, DVCC limits and all other paths remain part of the comparison. Each
interpreter uses a fake D-Bus publisher, frozen physical/wall clocks, and direct
MQTT callbacks; it never connects to MQTT or registers a real battery service.

The ordinary regression test runs the candidate and compares each state and
transaction hash with this frozen baseline. Do not regenerate the expected
output from a candidate simply to make a changed test pass. A deliberate
behavior change requires review and a separately justified contract update.

For detailed diagnosis, capture full publications from an immutable source
directory and the candidate, then compare the `scenarios` objects:

```bash
python tests/legacy_replay.py --source /path/to/immutable-baseline > baseline.json
python tests/legacy_replay.py --source /path/to/candidate > candidate.json
```

These are synthetic software regressions. They preserve existing behavior,
including historical configuration semantics; they do not qualify a new
electrical topology or replace on-device commissioning.
