# Documentation

A D-Bus service that turns MQTT battery telemetry into a Venus OS battery, with
the topology model, broker profile and release records.

## Operate it

- [Explicit series topologies](flexible-topology.md) — the opt-in mode that represents each physical battery and its cells.
- [Protected local broker profile](secure-local-broker.md) — the broker configuration the service expects.

## Assurance

- [Architecture, assurance evidence and roadmap](project-assurance.md) — how the pieces fit, what is evidenced and what is planned.
- [OpenSSF Best Practices evidence: dbus-mqtt-battery](openssf-evidence.md) — the index behind the assessment.

## Releases

- [Release operations](release-packaging.md) — what `release.sh` delegates and which events validate what.
- [Release version inputs](release-versioning.md) — the `versioning` fields that identify a release.
- [CI and release operations](release-workflow.md) — `.release-policy.json` and the required status.
- [CI orchestration](CI-ORCHESTRATION.md) — how the quality gate and release pipeline divide the events.
- [Version plans and manifest synchronization](VERSIONING.md) — the versioning opt-in.
- [Release-candidate fuzz testing](fuzzing.md) — the illustrated RC fuzzing process.
- [Fuzzing release candidates](rc-fuzzing.md) — the reusable bounded fuzz workflow.
- [Release read recovery and rerun validation](release-read-safety.md) — the two fixes carried from the shared toolkit.
- [Early final-build runner check](runner-preflight.md) — why the check runs before the final build.
