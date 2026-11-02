"""Bounded MQTT observation without constructing or registering a D-Bus service."""

from __future__ import annotations

from time import monotonic, sleep


def observe(client, seconds: int) -> dict:
    """Return readiness and final measurements; this never changes battery control."""
    deadline = monotonic() + seconds
    complete = unavailable = 0
    final = None
    while monotonic() < deadline:
        final = client.get_aggregate_data()
        if final and final["data_complete"]:
            complete += 1
        else:
            unavailable += 1
        sleep(min(1.0, max(0.0, deadline - monotonic())))
    return {
        "mode": "observation_no_dbus",
        "seconds": seconds,
        "complete_samples": complete,
        "unavailable_samples": unavailable,
        "final": final,
        "diagnostic": client.missing_data_reason(),
    }
