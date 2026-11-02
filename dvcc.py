"""
DVCC Controller - Dynamic Voltage and Current Control
======================================================

Extractable module for calculating charge/discharge limits based on:
- Cell voltage thresholds
- Cell imbalance
- Temperature limits
- SoC (optional)

This module has NO D-Bus or MQTT dependencies - pure Python for easy testing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from time import monotonic
from typing import Any

# =============================================================================
# CONFIGURATION DEFAULTS (can be overridden in constructor)
# =============================================================================

DVCC_CELL_FULL_CURRENT = 3.40  # V - Below this: 100% charge current
DVCC_CELL_START_LIMIT = 3.45  # V - Start reducing current
DVCC_CELL_BALANCE_VOLTAGE = 3.50  # V - Aggressive reduction, balancers working
DVCC_CELL_NEAR_FULL = 3.55  # V - Minimal current (tail charge)
DVCC_CELL_CUTOFF = 3.60  # V - Stop charging completely

# Maximum currents (adjust for your battery system)
DVCC_MAX_CHARGE_CURRENT = 100.0  # A - Maximum charge current at normal conditions
DVCC_MAX_DISCHARGE_CURRENT = 120.0  # A - Maximum discharge current
DVCC_MIN_CHARGE_CURRENT = 2.0  # A - Minimum tail charge current (for balancing)

# CVL settings (Charge Voltage Limit)
DVCC_CELL_MAX_VOLTAGE = 3.65  # V - Maximum cell voltage (for CVL calculation)
DVCC_CELLS_PER_BMS = 4  # Cells per BMS module

# Cell imbalance protection
DVCC_IMBALANCE_START_LIMIT = 0.05  # V - Start reducing current if delta > this
DVCC_IMBALANCE_AGGRESSIVE = 0.10  # V - Aggressive reduction
DVCC_IMBALANCE_CRITICAL = 0.20  # V - Minimal current

# Temperature-based current limiting
DVCC_TEMP_FULL_CURRENT_MIN = 10  # °C - Full current above this temp
DVCC_TEMP_FULL_CURRENT_MAX = 40  # °C - Full current below this temp
DVCC_TEMP_STOP_CHARGE = 0  # °C - Stop charging below this temp
DVCC_TEMP_STOP_CHARGE_HIGH = 50  # °C - Stop charging above this temp

# SoC-based reduction (optional, for extending battery life)
DVCC_SOC_REDUCE_START = 95  # % - Start reducing current above this SoC
DVCC_SOC_REDUCE_FACTOR = 0.5  # Factor at 100% SoC (0.5 = 50% of max current)


@dataclass(frozen=True)
class DvccProfile:
    """Explicit LiFePO4 control curve for a commissioned series topology.

    Defaults document the established curve. External configuration must provide
    every field, so a partial profile cannot inherit an unnoticed control limit.
    This validates consistency, not the manufacturer's approval for a battery.
    """

    chemistry: str = "lifepo4"
    max_charge_current: float = DVCC_MAX_CHARGE_CURRENT
    max_discharge_current: float = DVCC_MAX_DISCHARGE_CURRENT
    min_charge_current: float = DVCC_MIN_CHARGE_CURRENT
    cell_max_voltage: float = DVCC_CELL_MAX_VOLTAGE
    cell_full_current: float = DVCC_CELL_FULL_CURRENT
    cell_start_limit: float = DVCC_CELL_START_LIMIT
    cell_balance_voltage: float = DVCC_CELL_BALANCE_VOLTAGE
    cell_near_full: float = DVCC_CELL_NEAR_FULL
    cell_cutoff: float = DVCC_CELL_CUTOFF
    charge_balance_factor: float = 0.20
    imbalance_start_limit: float = DVCC_IMBALANCE_START_LIMIT
    imbalance_aggressive: float = DVCC_IMBALANCE_AGGRESSIVE
    imbalance_critical: float = DVCC_IMBALANCE_CRITICAL
    imbalance_aggressive_factor: float = 0.30
    temp_full_current_min: float = DVCC_TEMP_FULL_CURRENT_MIN
    temp_full_current_max: float = DVCC_TEMP_FULL_CURRENT_MAX
    temp_stop_charge: float = DVCC_TEMP_STOP_CHARGE
    temp_stop_charge_high: float = DVCC_TEMP_STOP_CHARGE_HIGH
    cold_charge_factor: float = 0.5
    hot_charge_min_factor: float = 0.2
    soc_reduce_start: float = DVCC_SOC_REDUCE_START
    soc_reduce_factor: float = DVCC_SOC_REDUCE_FACTOR
    discharge_cell_full_current: float = 3.0
    discharge_cell_start_limit: float = 2.9
    discharge_cell_cutoff: float = 2.7
    discharge_low_factor: float = 0.5
    discharge_hot_factor: float = 0.5
    ccl_change_rate: float = 10.0

    def __post_init__(self) -> None:
        if self.chemistry != "lifepo4":
            raise ValueError("only lifepo4 protection profiles are supported")
        for item in fields(self):
            if item.name == "chemistry":
                continue
            value = getattr(self, item.name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{item.name} must be a finite number")  # noqa: TRY004 - config contract
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{item.name} must be a finite number")
            # The supported inventory is at most 128 cells. Leave room for
            # cell-count scaling and centivolt precision before publication.
            if not math.isfinite(float(value) * 12800):
                raise ValueError(f"{item.name} exceeds the supported numeric range")
        if min(self.max_charge_current, self.max_discharge_current, self.ccl_change_rate) <= 0:
            raise ValueError("current ceilings and recovery rate must be positive")
        if not (
            0
            < self.discharge_cell_cutoff
            < self.discharge_cell_start_limit
            < self.discharge_cell_full_current
            < self.cell_full_current
            <= self.cell_start_limit
            < self.cell_balance_voltage
            < self.cell_near_full
            < self.cell_cutoff
            <= self.cell_max_voltage
        ):
            raise ValueError("cell-voltage thresholds must have an ordered operating range")
        if (
            not 0
            <= self.imbalance_start_limit
            < self.imbalance_aggressive
            < self.imbalance_critical
        ):
            raise ValueError("imbalance thresholds must increase")
        if not (
            -273.15
            < self.temp_stop_charge
            < self.temp_full_current_min
            <= self.temp_full_current_max
            < self.temp_stop_charge_high
        ):
            raise ValueError("temperature thresholds must have an ordered operating range")
        if not 0 <= self.soc_reduce_start < 100:
            raise ValueError("soc_reduce_start must be between zero and 100 exclusive")
        for name in (
            "charge_balance_factor",
            "imbalance_aggressive_factor",
            "cold_charge_factor",
            "hot_charge_min_factor",
            "soc_reduce_factor",
            "discharge_low_factor",
            "discharge_hot_factor",
        ):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if self.charge_balance_factor == 0 or self.imbalance_aggressive_factor == 0:
            raise ValueError("charge taper factors must be positive")
        taper_ceiling = self.max_charge_current * min(
            self.charge_balance_factor, self.imbalance_aggressive_factor
        )
        if not 0 <= self.min_charge_current <= taper_ceiling:
            raise ValueError("tail current must not exceed either charge taper ceiling")

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> DvccProfile:
        """Reject incomplete profiles and misspelled fields before enabling control."""
        if not isinstance(values, dict):
            raise ValueError("protection_profile must be an object")  # noqa: TRY004 - config contract
        expected = {item.name for item in fields(cls)}
        if set(values) != expected:
            missing = sorted(expected - set(values))
            unknown = sorted(set(values) - expected, key=str)
            raise ValueError(f"protection profile fields: missing={missing}, unknown={unknown}")
        return cls(**values)


class DvccController:
    """
    Dynamic Voltage and Current Control for cell protection.

    Calculates CCL (Charge Current Limit) and DCL (Discharge Current Limit)
    based on:
    - Highest cell voltage (most critical for charge protection)
    - Cell voltage imbalance (delta between min and max cells)
    - Temperature limits
    - SoC (optional, for battery longevity)

    The goal is to protect cells BEFORE BMS triggers emergency cutoff,
    allowing balancers time to work and preventing system shutdowns.
    """

    def __init__(
        self,
        cell_count: int,
        bms_count: int = 1,
        max_charge_current: float = DVCC_MAX_CHARGE_CURRENT,
        max_discharge_current: float = DVCC_MAX_DISCHARGE_CURRENT,
        cell_max_voltage: float = DVCC_CELL_MAX_VOLTAGE,
        min_charge_current: float = DVCC_MIN_CHARGE_CURRENT,
        *,
        profile: DvccProfile | None = None,
    ):
        self.profile = profile
        self._curve = profile if profile is not None else DvccProfile()
        if profile is not None:
            if not isinstance(profile, DvccProfile):
                raise ValueError("profile must be a validated DvccProfile")
            if type(cell_count) is not int or not 1 <= cell_count <= 128:
                raise ValueError("profile cell_count must be an integer between 1 and 128")
            if type(bms_count) is not int or not 1 <= bms_count <= 4:
                raise ValueError("profile bms_count must be an integer between 1 and 4")
            max_charge_current = profile.max_charge_current
            max_discharge_current = profile.max_discharge_current
            cell_max_voltage = profile.cell_max_voltage
            min_charge_current = profile.min_charge_current
        self.cell_count = cell_count
        self.bms_count = bms_count
        self._max_charge_current = max_charge_current
        self._max_discharge_current = max_discharge_current
        self._cell_max_voltage = cell_max_voltage
        self._min_charge_current = min_charge_current

        self.last_ccl = max_charge_current
        self.last_dcl = max_discharge_current
        self.last_cvl = cell_max_voltage * cell_count

        # Rate limiting for smooth transitions
        # Protective reductions remain immediate; only increases are rate limited.
        self.ccl_change_rate = self._curve.ccl_change_rate
        self.last_update_time = monotonic()

    def calculate_ccl_from_cell_voltage(self, max_cell_voltage: float | None) -> tuple[float, str]:
        """
        Calculate CCL based on highest cell voltage.
        Returns (current_limit, reason_string).

        Uses linear interpolation between voltage thresholds for smooth control.
        """
        if max_cell_voltage is None:
            return self._max_charge_current, "no_cell_data"

        v = max_cell_voltage
        max_cc = self._max_charge_current
        min_cc = self._min_charge_current

        # Below threshold - full current
        if v <= self._curve.cell_full_current:
            return max_cc, "normal"

        # Cell cutoff - stop charging
        if v >= self._curve.cell_cutoff:
            return 0.0, f"cell_overvoltage_{v:.3f}V"

        # Near full - minimal current for balancing
        if v >= self._curve.cell_near_full:
            # Linear reduction from MIN_CHARGE_CURRENT to 0 between NEAR_FULL and CUTOFF
            factor = 1.0 - (v - self._curve.cell_near_full) / (
                self._curve.cell_cutoff - self._curve.cell_near_full
            )
            ccl = min_cc * factor
            return max(0.0, ccl), f"tail_charge_{v:.3f}V"

        # Balance voltage - aggressive reduction
        if v >= self._curve.cell_balance_voltage:
            # Linear reduction from ~20% to MIN_CHARGE_CURRENT
            factor = 1.0 - (v - self._curve.cell_balance_voltage) / (
                self._curve.cell_near_full - self._curve.cell_balance_voltage
            )
            ccl = min_cc + (max_cc * self._curve.charge_balance_factor - min_cc) * factor
            return ccl, f"balancing_{v:.3f}V"

        # Start limiting - gradual reduction
        if v >= self._curve.cell_start_limit:
            # Linear reduction from 100% to 20%
            factor = 1.0 - (v - self._curve.cell_start_limit) / (
                self._curve.cell_balance_voltage - self._curve.cell_start_limit
            )
            ccl = max_cc * (
                self._curve.charge_balance_factor
                + (1.0 - self._curve.charge_balance_factor) * factor
            )
            return ccl, f"reducing_{v:.3f}V"

        # Between FULL_CURRENT and START_LIMIT - full current
        return max_cc, "normal"

    def calculate_ccl_from_imbalance(self, cell_delta: float | None) -> tuple[float, str]:
        """
        Calculate CCL reduction based on cell voltage imbalance.
        Returns (current_limit, reason_string).

        High imbalance indicates one cell is "running away" and needs
        time for balancers to catch up.
        """
        if cell_delta is None or cell_delta < 0:
            return self._max_charge_current, "no_delta"

        max_cc = self._max_charge_current
        min_cc = self._min_charge_current

        # Normal imbalance
        if cell_delta <= self._curve.imbalance_start_limit:
            return max_cc, "balanced"

        # Critical imbalance
        if cell_delta >= self._curve.imbalance_critical:
            return min_cc, f"critical_imbalance_{cell_delta:.3f}V"

        # Aggressive zone
        if cell_delta >= self._curve.imbalance_aggressive:
            factor = 1.0 - (cell_delta - self._curve.imbalance_aggressive) / (
                self._curve.imbalance_critical - self._curve.imbalance_aggressive
            )
            ccl = min_cc + (max_cc * self._curve.imbalance_aggressive_factor - min_cc) * factor
            return ccl, f"imbalance_{cell_delta:.3f}V"

        # Start limiting zone
        factor = 1.0 - (cell_delta - self._curve.imbalance_start_limit) / (
            self._curve.imbalance_aggressive - self._curve.imbalance_start_limit
        )
        ccl = max_cc * (
            self._curve.imbalance_aggressive_factor
            + (1.0 - self._curve.imbalance_aggressive_factor) * factor
        )
        return ccl, f"slight_imbalance_{cell_delta:.3f}V"

    def calculate_ccl_from_temperature(
        self, min_temp: float | None, max_temp: float | None
    ) -> tuple[float, str]:
        """
        Calculate CCL based on temperature limits.
        Returns (current_limit, reason_string).

        LiFePO4 should not be charged below 0°C (lithium plating risk)
        and should have reduced current at high temperatures.
        """
        if min_temp is None:
            min_temp = 25.0
        if max_temp is None:
            max_temp = 25.0

        max_cc = self._max_charge_current

        # Too cold - stop charging
        if min_temp <= self._curve.temp_stop_charge:
            return 0.0, f"too_cold_{min_temp:.1f}C"

        # Too hot - stop charging
        if max_temp >= self._curve.temp_stop_charge_high:
            return 0.0, f"too_hot_{max_temp:.1f}C"

        # Cold but chargeable - reduce current
        if min_temp < self._curve.temp_full_current_min:
            factor = (min_temp - self._curve.temp_stop_charge) / (
                self._curve.temp_full_current_min - self._curve.temp_stop_charge
            )
            ccl = max_cc * factor * self._curve.cold_charge_factor
            return ccl, f"cold_{min_temp:.1f}C"

        # Hot - reduce current
        if max_temp > self._curve.temp_full_current_max:
            factor = 1.0 - (max_temp - self._curve.temp_full_current_max) / (
                self._curve.temp_stop_charge_high - self._curve.temp_full_current_max
            )
            ccl = max_cc * max(self._curve.hot_charge_min_factor, factor)
            return ccl, f"hot_{max_temp:.1f}C"

        return max_cc, "temp_ok"

    def calculate_ccl_from_soc(self, soc: float | None) -> tuple[float, str]:
        """
        Calculate CCL based on SoC (optional battery longevity feature).
        Reduces current at high SoC to extend battery life.
        """
        if soc is None or soc < self._curve.soc_reduce_start:
            return self._max_charge_current, "soc_ok"

        max_cc = self._max_charge_current

        if soc >= 100.0:
            return max_cc * self._curve.soc_reduce_factor, "soc_100"

        # Linear reduction from 100% to REDUCE_FACTOR
        factor = 1.0 - (soc - self._curve.soc_reduce_start) / (
            100.0 - self._curve.soc_reduce_start
        ) * (1.0 - self._curve.soc_reduce_factor)
        return max_cc * factor, f"soc_{soc:.0f}"

    def calculate_dcl_from_cell_voltage(self, min_cell_voltage: float | None) -> tuple[float, str]:
        """
        Calculate DCL based on lowest cell voltage.
        Returns (current_limit, reason_string).

        Protects cells from over-discharge.
        """
        if min_cell_voltage is None:
            return self._max_discharge_current, "no_cell_data"

        v = min_cell_voltage
        max_dc = self._max_discharge_current

        # Normal voltage - full discharge
        if v >= self._curve.discharge_cell_full_current:
            return max_dc, "normal"

        # Critical - stop discharge
        if v <= self._curve.discharge_cell_cutoff:
            return 0.0, f"cell_undervoltage_{v:.3f}V"

        # Reduce discharge as voltage drops
        if v <= self._curve.discharge_cell_start_limit:
            factor = (v - self._curve.discharge_cell_cutoff) / (
                self._curve.discharge_cell_start_limit - self._curve.discharge_cell_cutoff
            )
            dcl = max_dc * factor * self._curve.discharge_low_factor
            return dcl, f"low_cell_{v:.3f}V"

        # Slight reduction
        factor = (v - self._curve.discharge_cell_start_limit) / (
            self._curve.discharge_cell_full_current - self._curve.discharge_cell_start_limit
        )
        dcl = max_dc * (
            self._curve.discharge_low_factor + (1.0 - self._curve.discharge_low_factor) * factor
        )
        return dcl, f"reducing_{v:.3f}V"

    def calculate(self, data: dict) -> dict:
        """
        Calculate all DVCC parameters based on battery data.

        Returns dict with:
            - ccl: Charge Current Limit (A)
            - dcl: Discharge Current Limit (A)
            - cvl: Charge Voltage Limit (V)
            - ccl_reason: Why CCL was limited
            - dcl_reason: Why DCL was limited
            - max_cell_id: ID of the highest voltage cell
            - min_cell_id: ID of the lowest voltage cell
        """
        max_cell = data.get("max_cell")
        min_cell = data.get("min_cell")
        max_cell_id = data.get("max_cell_id")
        min_cell_id = data.get("min_cell_id")
        max_temp = data.get("max_temp")
        min_temp = data.get("min_temp")
        soc = data.get("soc")

        # Calculate cell delta (imbalance)
        cell_delta = None
        if max_cell is not None and min_cell is not None:
            cell_delta = max_cell - min_cell

        # Calculate CCL from all sources
        ccl_voltage, reason_voltage = self.calculate_ccl_from_cell_voltage(max_cell)
        ccl_imbalance, reason_imbalance = self.calculate_ccl_from_imbalance(cell_delta)
        ccl_temp, reason_temp = self.calculate_ccl_from_temperature(min_temp, max_temp)
        ccl_soc, reason_soc = self.calculate_ccl_from_soc(soc)

        # Take minimum of all CCL calculations (most restrictive wins)
        ccl_values = [
            (ccl_voltage, reason_voltage),
            (ccl_imbalance, reason_imbalance),
            (ccl_temp, reason_temp),
            (ccl_soc, reason_soc),
        ]

        ccl, ccl_reason = min(ccl_values, key=lambda x: x[0])

        # Calculate DCL
        dcl, dcl_reason = self.calculate_dcl_from_cell_voltage(min_cell)

        # Temperature-based DCL reduction
        if max_temp is not None and max_temp >= self._curve.temp_stop_charge_high:
            dcl = min(dcl, self._max_discharge_current * self._curve.discharge_hot_factor)
            dcl_reason = f"hot_{max_temp:.1f}C"

        # Smooth increases only: never exceed a newly calculated safety ceiling.
        now = monotonic()
        dt = max(0.0, now - self.last_update_time)
        self.last_update_time = now

        max_change = self.ccl_change_rate * dt
        if ccl > self.last_ccl:
            ccl = min(ccl, self.last_ccl + max_change)

        # BMS blocks MUST be applied AFTER rate limiting
        # because rate limiting can increase values on first call
        if not data.get("allow_charge", True):
            ccl = 0.0
            ccl_reason = "bms_blocked"

        if not data.get("allow_discharge", True):
            dcl = 0.0
            dcl_reason = "bms_blocked"
        self.last_ccl = ccl
        self.last_dcl = dcl

        # Calculate CVL (Charge Voltage Limit)
        cvl = self._cell_max_voltage * self.cell_count

        if self.profile is not None:
            # A configured fractional ceiling must never be rounded upward.
            # The historical no-profile output retains its original rounding.
            ccl = math.floor(ccl * 10) / 10
            dcl = math.floor(dcl * 10) / 10
            cvl = math.floor(cvl * 100) / 100

        return {
            "ccl": round(ccl, 1),
            "dcl": round(dcl, 1),
            "cvl": round(cvl, 2),
            "ccl_reason": ccl_reason,
            "dcl_reason": dcl_reason,
            "max_cell_voltage": max_cell,
            "max_cell_id": max_cell_id,
            "min_cell_voltage": min_cell,
            "min_cell_id": min_cell_id,
            "cell_delta": cell_delta,
        }
