"""Tests for BatteryData container."""

# pylint: disable=missing-class-docstring,missing-function-docstring

from dbus_mqtt_battery.bms_data import BatteryData


class TestUpdate:
    def test_voltage(self):
        b = BatteryData(1)
        b.update("voltage", "12.5")
        assert b.voltage == 12.5

    def test_soc(self):
        b = BatteryData(1)
        b.update("soc", "75.0")
        assert b.soc == 75.0

    def test_cycles_int(self):
        b = BatteryData(1)
        b.update("cycles", "42")
        assert b.cycles == 42
        assert isinstance(b.cycles, int)

    def test_charging_on_string(self):
        b = BatteryData(1)
        b.update("charging", "ON")
        assert b.charging is True

    def test_charging_true_string(self):
        b = BatteryData(1)
        b.update("charging", "TRUE")
        assert b.charging is True

    def test_charging_off_string(self):
        b = BatteryData(1)
        b.update("charging", "OFF")
        assert b.charging is False

    def test_cell_voltage(self):
        b = BatteryData(1)
        b.update("cell_1", "3.30")
        b.update("cell_2", "3.31")
        assert b.cells[1] == 3.30
        assert b.cells[2] == 3.31
        # cell_count tracks max observed across all updates; starts at 4 by default
        assert b.cell_count >= 2

    def test_temperature_with_index(self):
        b = BatteryData(1)
        b.update("temperature_1", "25.0")
        b.update("temperature_2", "27.0")
        assert b.temperatures[1] == 25.0
        assert b.temperatures[2] == 27.0
        assert b.temperature == 26.0  # avg

    def test_unknown_key_ignored(self):
        b = BatteryData(1)
        b.update("garbage_key", "garbage")
        # no attribute added, no exception
        assert not hasattr(b, "garbage_key")

    def test_invalid_cell_key_ignored(self):
        b = BatteryData(1)
        b.update("cell_xyz", "3.30")
        assert not b.cells

    def test_invalid_temperature_key_ignored(self):
        b = BatteryData(1)
        b.update("temperature_abc", "25.0")
        # not added, but doesn't crash
        assert not b.temperatures

    def test_last_update_advances(self):
        b = BatteryData(1)
        assert b.last_update == 0.0
        b.update("voltage", "12.0")
        assert b.last_update > 0.0


class TestIsValid:
    def test_invalid_when_no_update(self):
        assert BatteryData(1).is_valid() is False

    def test_invalid_when_zero_voltage(self):
        b = BatteryData(1)
        b.update("voltage", "0.0")
        assert b.is_valid() is False

    def test_valid_when_fresh_and_positive(self):
        b = BatteryData(1)
        b.update("voltage", "12.0")
        assert b.is_valid() is True


class TestCellExtrema:
    def test_min_cell(self):
        b = BatteryData(1)
        b.update("cell_1", "3.30")
        b.update("cell_2", "3.25")
        b.update("cell_3", "3.35")
        voltage, idx = b.get_min_cell_voltage()
        assert voltage == 3.25
        assert idx == 2

    def test_max_cell(self):
        b = BatteryData(1)
        b.update("cell_1", "3.30")
        b.update("cell_2", "3.25")
        b.update("cell_3", "3.35")
        voltage, idx = b.get_max_cell_voltage()
        assert voltage == 3.35
        assert idx == 3

    def test_no_cells_returns_none(self):
        b = BatteryData(1)
        assert b.get_min_cell_voltage() == (None, None)
        assert b.get_max_cell_voltage() == (None, None)

    def test_zero_cells_ignored(self):
        b = BatteryData(1)
        b.update("cell_1", "0")
        assert b.get_min_cell_voltage() == (None, None)
