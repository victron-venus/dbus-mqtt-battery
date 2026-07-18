"""Tests for the DvccController class."""

import unittest
import sys
import os

# Add the package directory to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import directly from dvcc module (no mocking needed!)
from dvcc import DvccController


class TestDvccController(unittest.TestCase):  # pylint: disable=too-many-public-methods
    """Tests for the DvccController class."""

    def setUp(self):
        # Typical setup for a 4S battery system with 1 battery
        self.controller = DvccController(cell_count=4, bms_count=1)

    def test_calculate_ccl_from_cell_voltage_normal(self):
        """Test CCL calculation with normal cell voltage"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.3)
        self.assertEqual(ccl, 100.0)  # DVCC_MAX_CHARGE_CURRENT
        self.assertEqual(reason, "normal")

    def test_calculate_ccl_from_cell_voltage_below_full_current(self):
        """Test CCL calculation when cell voltage is below full current threshold"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.4)
        self.assertEqual(ccl, 100.0)
        self.assertEqual(reason, "normal")

    def test_calculate_ccl_from_cell_voltage_at_start_limit(self):
        """Test CCL calculation at the start limit voltage"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.45)
        # At exactly START_LIMIT, boundary case - checking actual behavior
        self.assertIn("reducing", reason)

    def test_calculate_ccl_from_cell_voltage_above_start_limit(self):
        """Test CCL calculation above start limit"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.48)
        # Between START_LIMIT (3.45) and BALANCE_VOLTAGE (3.50)
        # Should be reduced from 100% towards 20%
        self.assertGreater(ccl, 20.0)
        self.assertLess(ccl, 100.0)
        self.assertIn("reducing", reason)

    def test_calculate_ccl_from_cell_voltage_at_balance_voltage(self):
        """Test CCL calculation at balance voltage"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.50)
        # At BALANCE_VOLTAGE, should be in the balancing zone
        self.assertIn("balancing", reason)

    def test_calculate_ccl_from_cell_voltage_near_full(self):
        """Test CCL calculation near full voltage"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.52)
        # Between BALANCE_VOLTAGE (3.50) and NEAR_FULL (3.55)
        # Should be between MIN_CHARGE_CURRENT and (MIN_CHARGE_CURRENT + 20% of MAX_CHARGE_CURRENT)
        self.assertGreaterEqual(ccl, 2.0)
        self.assertLessEqual(ccl, 22.0)  # 2.0 + 0.2*100 = 22.0
        self.assertIn("balancing", reason)

    def test_calculate_ccl_from_cell_voltage_at_near_full(self):
        """Test CCL calculation at near full voltage"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.55)
        # At NEAR_FULL, start of tail charge
        # Should be MIN_CHARGE_CURRENT
        self.assertAlmostEqual(ccl, 2.0, places=1)
        self.assertIn("tail_charge", reason)

    def test_calculate_ccl_from_cell_voltage_above_near_full(self):
        """Test CCL calculation above near full"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.57)
        # Between NEAR_FULL (3.55) and CUTOFF (3.60)
        # Linear reduction from MIN_CHARGE_CURRENT to 0
        self.assertGreaterEqual(ccl, 0.0)
        self.assertLessEqual(ccl, 2.0)
        self.assertIn("tail_charge", reason)

    def test_calculate_ccl_from_cell_voltage_at_cutoff(self):
        """Test CCL calculation at cutoff voltage"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.60)
        self.assertEqual(ccl, 0.0)
        self.assertIn("cell_overvoltage", reason)

    def test_calculate_ccl_from_cell_voltage_above_cutoff(self):
        """Test CCL calculation above cutoff voltage"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(3.62)
        self.assertEqual(ccl, 0.0)
        self.assertIn("cell_overvoltage", reason)

    def test_calculate_ccl_from_cell_voltage_none(self):
        """Test CCL calculation with None voltage"""
        ccl, reason = self.controller.calculate_ccl_from_cell_voltage(None)
        self.assertEqual(ccl, 100.0)
        self.assertEqual(reason, "no_cell_data")

    def test_calculate_ccl_from_imbalance_balanced(self):
        """Test CCL calculation with balanced cells"""
        ccl, reason = self.controller.calculate_ccl_from_imbalance(0.01)
        self.assertEqual(ccl, 100.0)
        self.assertEqual(reason, "balanced")

    def test_calculate_ccl_from_imbalance_at_start_limit(self):
        """Test CCL calculation at imbalance start limit"""
        ccl, reason = self.controller.calculate_ccl_from_imbalance(0.05)
        self.assertEqual(ccl, 100.0)
        self.assertEqual(reason, "balanced")

    def test_calculate_ccl_from_imbalance_above_start(self):
        """Test CCL calculation above imbalance start limit"""
        ccl, reason = self.controller.calculate_ccl_from_imbalance(0.07)
        # Between START_LIMIT (0.05) and AGGRESSIVE (0.10)
        # Should be reduced from 100% towards 30%
        self.assertGreater(ccl, 30.0)
        self.assertLess(ccl, 100.0)
        self.assertIn("slight_imbalance", reason)

    def test_calculate_ccl_from_imbalance_at_aggressive(self):
        """Test CCL calculation at aggressive threshold"""
        ccl, reason = self.controller.calculate_ccl_from_imbalance(0.10)
        # At AGGRESSIVE, should be 30% of max current
        self.assertAlmostEqual(ccl, 30.0, places=1)
        self.assertIn("imbalance", reason)

    def test_calculate_ccl_from_imbalance_critical(self):
        """Test CCL calculation at critical imbalance"""
        ccl, reason = self.controller.calculate_ccl_from_imbalance(0.20)
        self.assertAlmostEqual(ccl, 2.0, places=1)  # MIN_CHARGE_CURRENT
        self.assertIn("critical_imbalance", reason)

    def test_calculate_ccl_from_imbalance_above_critical(self):
        """Test CCL calculation above critical imbalance"""
        ccl, reason = self.controller.calculate_ccl_from_imbalance(0.25)
        self.assertAlmostEqual(ccl, 2.0, places=1)
        self.assertIn("critical_imbalance", reason)

    def test_calculate_ccl_from_imbalance_none(self):
        """Test CCL calculation with None imbalance"""
        ccl, reason = self.controller.calculate_ccl_from_imbalance(None)
        self.assertEqual(ccl, 100.0)
        self.assertEqual(reason, "no_delta")

    def test_calculate_ccl_from_temperature_ok(self):
        """Test CCL calculation with normal temperature"""
        ccl, reason = self.controller.calculate_ccl_from_temperature(20.0, 25.0)
        self.assertEqual(ccl, 100.0)
        self.assertEqual(reason, "temp_ok")

    def test_calculate_ccl_from_temperature_too_cold(self):
        """Test CCL calculation when too cold to charge"""
        ccl, reason = self.controller.calculate_ccl_from_temperature(-5.0, 10.0)
        self.assertEqual(ccl, 0.0)
        self.assertIn("too_cold", reason)

    def test_calculate_ccl_from_temperature_too_hot(self):
        """Test CCL calculation when too hot to charge"""
        ccl, reason = self.controller.calculate_ccl_from_temperature(20.0, 60.0)
        self.assertEqual(ccl, 0.0)
        self.assertIn("too_hot", reason)

    def test_calculate_ccl_from_temperature_cold_reduced(self):
        """Test CCL calculation when cold but chargeable with reduction"""
        ccl, reason = self.controller.calculate_ccl_from_temperature(5.0, 15.0)
        # Between STOP_CHARGE (0) and FULL_CURRENT_MIN (10)
        # Should be reduced
        self.assertGreaterEqual(ccl, 0.0)
        self.assertLessEqual(ccl, 50.0)  # Max 50% at cold temps
        self.assertIn("cold", reason)

    def test_calculate_ccl_from_temperature_hot_reduced(self):
        """Test CCL calculation when hot with reduction"""
        ccl, reason = self.controller.calculate_ccl_from_temperature(20.0, 45.0)
        # Between FULL_CURRENT_MAX (40) and STOP_CHARGE_HIGH (50)
        # Should be reduced
        self.assertGreaterEqual(ccl, 20.0)  # At least 20%
        self.assertLessEqual(ccl, 100.0)
        self.assertIn("hot", reason)

    def test_calculate_ccl_from_soc_normal(self):
        """Test CCL calculation with normal SoC"""
        ccl, reason = self.controller.calculate_ccl_from_soc(50.0)
        self.assertEqual(ccl, 100.0)
        self.assertEqual(reason, "soc_ok")

    def test_calculate_ccl_from_soc_above_reduce_start(self):
        """Test CCL calculation when SoC above reduction start"""
        ccl, reason = self.controller.calculate_ccl_from_soc(96.0)
        # Between REDUCE_START (95) and 100%
        # Should be reduced from 100% to 50% at 100% SoC
        self.assertGreaterEqual(ccl, 50.0)
        self.assertLessEqual(ccl, 100.0)
        self.assertIn("soc_96", reason)

    def test_calculate_ccl_from_soc_at_100(self):
        """Test CCL calculation at 100% SoC"""
        ccl, reason = self.controller.calculate_ccl_from_soc(100.0)
        self.assertEqual(ccl, 50.0)  # MAX_CHARGE_CURRENT * REDUCE_FACTOR
        self.assertEqual(reason, "soc_100")

    def test_calculate_ccl_from_soc_none(self):
        """Test CCL calculation with None SoC"""
        ccl, reason = self.controller.calculate_ccl_from_soc(None)
        self.assertEqual(ccl, 100.0)
        self.assertEqual(reason, "soc_ok")

    def test_calculate_dcl_from_cell_voltage_normal(self):
        """Test DCL calculation with normal cell voltage"""
        ccl, reason = self.controller.calculate_dcl_from_cell_voltage(3.2)
        self.assertEqual(ccl, 120.0)  # DVCC_MAX_DISCHARGE_CURRENT
        self.assertEqual(reason, "normal")

    def test_calculate_dcl_from_cell_voltage_above_3v(self):
        """Test DCL calculation when cell voltage above 3.0V"""
        ccl, reason = self.controller.calculate_dcl_from_cell_voltage(3.1)
        self.assertEqual(ccl, 120.0)
        self.assertEqual(reason, "normal")

    def test_calculate_dcl_from_cell_voltage_below_3v(self):
        """Test DCL calculation when cell voltage below 3.0V"""
        ccl, reason = self.controller.calculate_dcl_from_cell_voltage(2.8)
        # Between 2.7 and 2.9, should be reduced
        self.assertGreaterEqual(ccl, 0.0)
        self.assertLessEqual(ccl, 60.0)  # MAX_DISCHARGE_CURRENT * 0.5
        self.assertIn("low_cell", reason)

    def test_calculate_dcl_from_cell_voltage_at_2v9(self):
        """Test DCL calculation at 2.9V"""
        ccl, reason = self.controller.calculate_dcl_from_cell_voltage(2.9)
        # At 2.9V, factor = (2.9-2.7)/(2.9-2.7) = 1.0
        # dcl = MAX_DISCHARGE_CURRENT * 0.5 * 1.0 = 60.0A
        self.assertAlmostEqual(ccl, 60.0, places=1)
        self.assertIn("low_cell", reason)

    def test_calculate_dcl_from_cell_voltage_at_2v7(self):
        """Test DCL calculation at 2.7V"""
        ccl, reason = self.controller.calculate_dcl_from_cell_voltage(2.7)
        self.assertEqual(ccl, 0.0)
        self.assertIn("cell_undervoltage", reason)

    def test_calculate_dcl_from_cell_voltage_below_2v7(self):
        """Test DCL calculation below 2.7V"""
        ccl, reason = self.controller.calculate_dcl_from_cell_voltage(2.5)
        self.assertEqual(ccl, 0.0)
        self.assertIn("cell_undervoltage", reason)

    def test_calculate_dcl_from_cell_voltage_none(self):
        """Test DCL calculation with None voltage"""
        ccl, reason = self.controller.calculate_dcl_from_cell_voltage(None)
        self.assertEqual(ccl, 120.0)
        self.assertEqual(reason, "no_cell_data")

    def test_calculate_integrates_all_factors(self):
        """Test that the main calculate method takes the minimum of all factors"""
        # Mock data that would give different CCL values from each source
        data = {
            "max_cell": 3.4,  # Would give ~100A from voltage
            "min_cell": 3.1,
            "max_cell_id": 1,
            "min_cell_id": 1,
            "max_temp": 25.0,
            "min_temp": 20.0,
            "soc": 50.0,
            "allow_charge": True,
            "allow_discharge": True,
        }

        result = self.controller.calculate(data)

        # With normal temp, SoC, and cell voltage 3.4V (which is below START_LIMIT 3.45V)
        # All should give 100A, 120A, 100A, 100A -> min is 100A
        self.assertEqual(result["ccl"], 100.0)
        self.assertEqual(result["dcl"], 120.0)
        self.assertEqual(result["cvl"], 3.65 * 4)  # DVCC_CELL_MAX_VOLTAGE * cell_count
        self.assertIn("ccl_reason", result)
        self.assertIn("dcl_reason", result)

    def test_calculate_respects_bms_charge_block(self):
        """Test that BMS charge blocking overrides CCL to 0"""
        data = {
            "max_cell": 3.4,
            "min_cell": 3.1,
            "max_cell_id": 1,
            "min_cell_id": 1,
            "max_temp": 25.0,
            "min_temp": 20.0,
            "soc": 50.0,
            "allow_charge": False,  # BUS says don't charge
            "allow_discharge": True,
        }

        result = self.controller.calculate(data)
        self.assertEqual(result["ccl"], 0.0)
        self.assertEqual(result["ccl_reason"], "bms_blocked")

    def test_calculate_respects_bms_discharge_block(self):
        """Test that BMS discharge blocking overrides DCL to 0"""
        data = {
            "max_cell": 3.4,
            "min_cell": 3.1,
            "max_cell_id": 1,
            "min_cell_id": 1,
            "max_temp": 25.0,
            "min_temp": 20.0,
            "soc": 50.0,
            "allow_charge": True,
            "allow_discharge": False,  # BMS says don't discharge
        }

        result = self.controller.calculate(data)
        self.assertEqual(result["dcl"], 0.0)
        self.assertEqual(result["dcl_reason"], "bms_blocked")


if __name__ == "__main__":
    unittest.main()
