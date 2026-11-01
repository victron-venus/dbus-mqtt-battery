"""Opt-in control profiles retain legacy behavior and enforce declared limits."""

from dataclasses import FrozenInstanceError, asdict, fields, replace
from itertools import pairwise
from unittest.mock import patch

import pytest

from dvcc import DvccController, DvccProfile


def custom_profile():
    """A complete, explicitly configured curve with lower equipment limits."""
    return replace(
        DvccProfile(),
        max_charge_current=60.0,
        max_discharge_current=80.0,
        min_charge_current=1.0,
        cell_max_voltage=3.60,
        cell_full_current=3.35,
        cell_start_limit=3.40,
        cell_near_full=3.54,
        cell_cutoff=3.58,
        temp_stop_charge=5.0,
        temp_full_current_min=15.0,
        temp_full_current_max=35.0,
        temp_stop_charge_high=45.0,
        soc_reduce_start=90.0,
        soc_reduce_factor=0.25,
        discharge_cell_cutoff=2.80,
        discharge_cell_start_limit=3.05,
        discharge_cell_full_current=3.20,
        discharge_low_factor=0.25,
        ccl_change_rate=2.0,
    )


def ready_data(**changes):
    data = {
        "max_cell": 3.32,
        "min_cell": 3.30,
        "max_temp": 25.0,
        "min_temp": 20.0,
        "soc": 50.0,
        "allow_charge": True,
        "allow_discharge": True,
        "max_cell_id": 16,
        "min_cell_id": 1,
    }
    data.update(changes)
    return data


def test_complete_profile_roundtrip_and_immutable_limits():
    profile = custom_profile()
    assert DvccProfile.from_mapping(asdict(profile)) == profile
    with pytest.raises(FrozenInstanceError):
        profile.max_charge_current = 1000


@pytest.mark.parametrize("change", ["missing", "unknown", "not_object"])
def test_partial_or_misspelled_external_profile_cannot_enable_control(change):
    values = asdict(DvccProfile())
    if change == "missing":
        del values["temp_stop_charge"]
    elif change == "unknown":
        values["max_charging_current"] = 200
    else:
        values = []
    with pytest.raises(ValueError):
        DvccProfile.from_mapping(values)


@pytest.mark.parametrize(
    "invalid", [float("inf"), float("-inf"), float("nan"), True, "3.65", 10**1000]
)
def test_every_numeric_profile_field_rejects_nonfinite_or_wrong_types(invalid):
    for item in fields(DvccProfile):
        if item.name == "chemistry":
            continue
        with pytest.raises(ValueError, match="finite number"):
            replace(DvccProfile(), **{item.name: invalid})


@pytest.mark.parametrize(
    "name", ["max_charge_current", "max_discharge_current", "cell_max_voltage", "ccl_change_rate"]
)
def test_finite_configuration_cannot_overflow_inventory_or_publication_scaling(name):
    with pytest.raises(ValueError, match="supported numeric range"):
        replace(DvccProfile(), **{name: 1e308})


@pytest.mark.parametrize(
    "changes",
    [
        {"chemistry": "nmc"},
        {"max_charge_current": 0},
        {"max_discharge_current": -1},
        {"ccl_change_rate": 0},
        {"min_charge_current": 21},
        {"cell_cutoff": 3.7},
        {"cell_near_full": 3.5},
        {"discharge_cell_cutoff": 2.9},
        {"discharge_cell_full_current": 3.5},
        {"imbalance_aggressive": 0.05},
        {"temp_stop_charge": 10},
        {"temp_full_current_max": 51},
        {"temp_stop_charge": -274},
        {"soc_reduce_start": 100},
        {"soc_reduce_factor": 1.1},
        {"discharge_hot_factor": -0.01},
        {"charge_balance_factor": 0},
    ],
)
def test_inconsistent_profiles_fail_before_controller_registration(changes):
    with pytest.raises(ValueError):
        replace(DvccProfile(), **changes)


@pytest.mark.parametrize("cells", [4, 8, 12, 16, 32, 128])
def test_cell_inventory_scales_voltage_without_multiplying_series_current(cells):
    controller = DvccController(cells, profile=custom_profile())
    limits = controller.calculate(ready_data())
    assert limits["cvl"] == round(3.60 * cells, 2)
    assert limits["ccl"] == 60.0
    assert limits["dcl"] == 80.0


def test_profile_voltage_temperature_and_soc_thresholds_are_active():
    controller = DvccController(16, profile=custom_profile())
    assert controller.calculate_ccl_from_cell_voltage(3.58)[0] == 0
    assert controller.calculate_dcl_from_cell_voltage(2.80)[0] == 0
    assert controller.calculate_dcl_from_cell_voltage(3.05)[0] == 20.0
    assert controller.calculate_ccl_from_temperature(5.0, 25.0)[0] == 0
    assert controller.calculate_ccl_from_temperature(25.0, 45.0)[0] == 0
    assert controller.calculate_ccl_from_soc(100.0)[0] == 15.0


def test_profile_taper_does_not_raise_limits_as_cells_approach_cutoff():
    controller = DvccController(16, profile=custom_profile())
    charge_limits = [
        controller.calculate_ccl_from_cell_voltage(3.30 + step / 1000)[0] for step in range(301)
    ]
    assert all(0 <= value <= 60 for value in charge_limits)
    assert all(left >= right for left, right in pairwise(charge_limits))
    discharge_limits = [
        controller.calculate_dcl_from_cell_voltage(3.30 - step / 1000)[0] for step in range(601)
    ]
    assert all(0 <= value <= 80 for value in discharge_limits)
    assert all(left >= right for left, right in pairwise(discharge_limits))


def test_equipment_cap_does_not_mutate_declared_profile_or_scale_with_pack_count():
    declared = custom_profile()
    effective = replace(declared, max_charge_current=20, max_discharge_current=30)
    controller = DvccController(16, bms_count=4, profile=effective)
    assert controller.calculate(ready_data())["ccl"] == 20.0
    assert controller.calculate(ready_data())["dcl"] == 30.0
    assert declared.max_charge_current == 60.0
    assert declared.max_discharge_current == 80.0


@pytest.mark.parametrize("charge,discharge", [(20.06, 30.08), (0.06, 0.06)])
def test_publication_precision_never_exceeds_fractional_equipment_ceiling(charge, discharge):
    profile = replace(
        custom_profile(),
        max_charge_current=charge,
        max_discharge_current=discharge,
        min_charge_current=0.01,
        cell_max_voltage=3.641,
    )
    limits = DvccController(16, profile=profile).calculate(ready_data())
    assert 0 <= limits["ccl"] <= charge
    assert 0 <= limits["dcl"] <= discharge
    assert limits["cvl"] <= 3.641 * 16
    if charge < 0.1:
        assert limits["ccl"] == limits["dcl"] == 0


def test_cold_ramp_transition_is_bounded_and_recovery_is_rate_limited():
    profile = custom_profile()
    controller = DvccController(16, profile=profile)
    for step in range(701):
        cold = -10 + step / 10
        limit, _ = controller.calculate_ccl_from_temperature(cold, max(cold, 25))
        assert 0 <= limit <= profile.max_charge_current
    with patch("dvcc.monotonic", return_value=10.0) as clock:
        controller = DvccController(16, profile=profile)
        initial = controller.calculate(ready_data(min_temp=14.99))["ccl"]
        clock.return_value = 11.0
        recovered = controller.calculate(ready_data(min_temp=15.0))["ccl"]
        assert recovered <= initial + profile.ccl_change_rate + 0.1
        assert recovered < profile.max_charge_current
        clock.return_value = 11.01
        assert controller.calculate(ready_data(min_temp=5.0))["ccl"] == 0


def test_recovery_rate_never_delays_reduction_or_overrides_mos_veto():
    with patch("dvcc.monotonic", return_value=10.0) as clock:
        controller = DvccController(16, profile=custom_profile())
        assert controller.calculate(ready_data())["ccl"] == 60.0
        clock.return_value = 10.01
        assert controller.calculate(ready_data(allow_charge=False))["ccl"] == 0.0
        clock.return_value = 11.01
        assert controller.calculate(ready_data())["ccl"] == 2.0
        clock.return_value = 11.02
        assert controller.calculate(ready_data(max_cell=3.58))["ccl"] == 0.0
        clock.return_value = 20.0
        limits = controller.calculate(ready_data(allow_charge=False, allow_discharge=False))
        assert limits["ccl"] == limits["dcl"] == 0.0
        assert limits["ccl_reason"] == limits["dcl_reason"] == "bms_blocked"


def test_explicit_baseline_curve_preserves_rules_with_conservative_publication():
    with patch("dvcc.monotonic", return_value=100.0) as clock:
        baseline = DvccController(16, bms_count=4)
        explicit = DvccController(16, bms_count=4, profile=DvccProfile())
        frames = [
            ready_data(),
            ready_data(max_cell=3.48, min_cell=3.45),
            ready_data(min_temp=1.0),
            ready_data(allow_charge=False),
            ready_data(),
            ready_data(max_temp=48.0),
            ready_data(max_cell=2.9, min_cell=2.8),
            ready_data(soc=98.0),
            ready_data(allow_discharge=False),
        ]
        for elapsed, frame in enumerate(frames, 1):
            clock.return_value = 100.0 + elapsed / 10
            actual, expected = explicit.calculate(frame), baseline.calculate(frame)
            assert explicit.last_ccl == baseline.last_ccl
            assert explicit.last_dcl == baseline.last_dcl
            for key, precision in (("ccl", 0.1), ("dcl", 0.1), ("cvl", 0.01)):
                assert expected[key] - precision - 1e-9 <= actual[key] <= expected[key]
                del actual[key]
                del expected[key]
            assert actual == expected


@pytest.mark.parametrize(
    "cells,members", [(0, 1), (True, 1), (4.0, 1), (129, 4), (4, 0), (4, True), (128, 5)]
)
def test_profile_requires_valid_static_inventory(cells, members):
    with pytest.raises(ValueError):
        DvccController(cells, bms_count=members, profile=custom_profile())
