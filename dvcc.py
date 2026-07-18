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

from typing import Dict, Optional, Tuple
from time import time

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
    ):
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
        self.ccl_change_rate = 10.0  # Max A/s change for CCL (smoothing)
        self.last_update_time = time()

    def calculate_ccl_from_cell_voltage(
        self, max_cell_voltage: Optional[float]
    ) -> Tuple[float, str]:
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
        if v <= DVCC_CELL_FULL_CURRENT:
            return max_cc, "normal"

        # Cell cutoff - stop charging
        if v >= DVCC_CELL_CUTOFF:
            return 0.0, f"cell_overvoltage_{v:.3f}V"

        # Near full - minimal current for balancing
        if v >= DVCC_CELL_NEAR_FULL:
            # Linear reduction from MIN_CHARGE_CURRENT to 0 between NEAR_FULL and CUTOFF
            factor = 1.0 - (v - DVCC_CELL_NEAR_FULL) / (DVCC_CELL_CUTOFF - DVCC_CELL_NEAR_FULL)
            ccl = min_cc * factor
            return max(0.0, ccl), f"tail_charge_{v:.3f}V"

        # Balance voltage - aggressive reduction
        if v >= DVCC_CELL_BALANCE_VOLTAGE:
            # Linear reduction from ~20% to MIN_CHARGE_CURRENT
            factor = 1.0 - (v - DVCC_CELL_BALANCE_VOLTAGE) / (
                DVCC_CELL_NEAR_FULL - DVCC_CELL_BALANCE_VOLTAGE
            )
            ccl = min_cc + (max_cc * 0.20 - min_cc) * factor
            return ccl, f"balancing_{v:.3f}V"

        # Start limiting - gradual reduction
        if v >= DVCC_CELL_START_LIMIT:
            # Linear reduction from 100% to 20%
            factor = 1.0 - (v - DVCC_CELL_START_LIMIT) / (
                DVCC_CELL_BALANCE_VOLTAGE - DVCC_CELL_START_LIMIT
            )
            ccl = max_cc * (0.20 + 0.80 * factor)
            return ccl, f"reducing_{v:.3f}V"

        # Between FULL_CURRENT and START_LIMIT - full current
        return max_cc, "normal"

    def calculate_ccl_from_imbalance(self, cell_delta: Optional[float]) -> Tuple[float, str]:
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
        if cell_delta <= DVCC_IMBALANCE_START_LIMIT:
            return max_cc, "balanced"

        # Critical imbalance
        if cell_delta >= DVCC_IMBALANCE_CRITICAL:
            return min_cc, f"critical_imbalance_{cell_delta:.3f}V"

        # Aggressive zone
        if cell_delta >= DVCC_IMBALANCE_AGGRESSIVE:
            factor = 1.0 - (cell_delta - DVCC_IMBALANCE_AGGRESSIVE) / (
                DVCC_IMBALANCE_CRITICAL - DVCC_IMBALANCE_AGGRESSIVE
            )
            ccl = min_cc + (max_cc * 0.30 - min_cc) * factor
            return ccl, f"imbalance_{cell_delta:.3f}V"

        # Start limiting zone
        factor = 1.0 - (cell_delta - DVCC_IMBALANCE_START_LIMIT) / (
            DVCC_IMBALANCE_AGGRESSIVE - DVCC_IMBALANCE_START_LIMIT
        )
        ccl = max_cc * (0.30 + 0.70 * factor)
        return ccl, f"slight_imbalance_{cell_delta:.3f}V"

    def calculate_ccl_from_temperature(
        self, min_temp: Optional[float], max_temp: Optional[float]
    ) -> Tuple[float, str]:
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
        if min_temp <= DVCC_TEMP_STOP_CHARGE:
            return 0.0, f"too_cold_{min_temp:.1f}C"

        # Too hot - stop charging
        if max_temp >= DVCC_TEMP_STOP_CHARGE_HIGH:
            return 0.0, f"too_hot_{max_temp:.1f}C"

        # Cold but chargeable - reduce current
        if min_temp < DVCC_TEMP_FULL_CURRENT_MIN:
            factor = (min_temp - DVCC_TEMP_STOP_CHARGE) / (
                DVCC_TEMP_FULL_CURRENT_MIN - DVCC_TEMP_STOP_CHARGE
            )
            ccl = max_cc * factor * 0.5  # Max 50% at cold temps
            return ccl, f"cold_{min_temp:.1f}C"

        # Hot - reduce current
        if max_temp > DVCC_TEMP_FULL_CURRENT_MAX:
            factor = 1.0 - (max_temp - DVCC_TEMP_FULL_CURRENT_MAX) / (
                DVCC_TEMP_STOP_CHARGE_HIGH - DVCC_TEMP_FULL_CURRENT_MAX
            )
            ccl = max_cc * max(0.2, factor)
            return ccl, f"hot_{max_temp:.1f}C"

        return max_cc, "temp_ok"

    def calculate_ccl_from_soc(self, soc: Optional[float]) -> Tuple[float, str]:
        """
        Calculate CCL based on SoC (optional battery longevity feature).
        Reduces current at high SoC to extend battery life.
        """
        if soc is None or soc < DVCC_SOC_REDUCE_START:
            return self._max_charge_current, "soc_ok"

        max_cc = self._max_charge_current

        if soc >= 100.0:
            return max_cc * DVCC_SOC_REDUCE_FACTOR, "soc_100"

        # Linear reduction from 100% to REDUCE_FACTOR
        factor = 1.0 - (soc - DVCC_SOC_REDUCE_START) / (100.0 - DVCC_SOC_REDUCE_START) * (
            1.0 - DVCC_SOC_REDUCE_FACTOR
        )
        return max_cc * factor, f"soc_{soc:.0f}"

    def calculate_dcl_from_cell_voltage(
        self, min_cell_voltage: Optional[float]
    ) -> Tuple[float, str]:
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
        if v >= 3.0:
            return max_dc, "normal"

        # Critical - stop discharge
        if v <= 2.7:
            return 0.0, f"cell_undervoltage_{v:.3f}V"

        # Reduce discharge as voltage drops
        if v <= 2.9:
            factor = (v - 2.7) / (2.9 - 2.7)
            dcl = max_dc * factor * 0.5
            return dcl, f"low_cell_{v:.3f}V"

        # Slight reduction
        factor = (v - 2.9) / (3.0 - 2.9)
        dcl = max_dc * (0.5 + 0.5 * factor)
        return dcl, f"reducing_{v:.3f}V"

    def calculate(self, data: Dict) -> Dict:
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
        if max_temp is not None and max_temp >= DVCC_TEMP_STOP_CHARGE_HIGH:
            dcl = min(dcl, self._max_discharge_current * 0.5)
            dcl_reason = f"hot_{max_temp:.1f}C"

        # Apply rate limiting for smooth transitions
        now = time()
        dt = now - self.last_update_time
        self.last_update_time = now

        max_change = self.ccl_change_rate * dt
        if ccl > self.last_ccl:
            ccl = min(ccl, self.last_ccl + max_change)
        elif ccl < self.last_ccl:
            # Allow faster reduction for safety
            ccl = max(ccl, self.last_ccl - max_change * 2)

        self.last_ccl = ccl
        self.last_dcl = dcl

        # BMS blocks MUST be applied AFTER rate limiting
        # because rate limiting can increase values on first call
        if not data.get("allow_charge", True):
            ccl = 0.0
            ccl_reason = "bms_blocked"

        if not data.get("allow_discharge", True):
            dcl = 0.0
            dcl_reason = "bms_blocked"
        self.last_dcl = dcl

        # Calculate CVL (Charge Voltage Limit)
        cvl = self._cell_max_voltage * self.cell_count

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
