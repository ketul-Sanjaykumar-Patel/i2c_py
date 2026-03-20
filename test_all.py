"""
tests/test_all.py
=================
Unit tests for i2c_lib — runs completely without hardware using MockHAL.

IMPROVEMENT OVER C VERSION:
  C has no tests. Python has pytest + MockHAL — every driver function
  is tested in CI on every push, with zero hardware required.

Run::

    pip install pytest
    pytest tests/ -v
"""

import struct
import pytest
from datetime import datetime
from unittest.mock import patch

from i2c_lib.bus import I2CBus, BusStats
from i2c_lib.exceptions import (
    DeviceNotFoundError, AddressError, I2CError
)
from i2c_lib.hal.backends import MockHAL
from i2c_lib.drivers.devices import (
    BME280, MPU6050, VEML7700, SSD1306,
    INA226, DS3231, AT24C32, PCF8574
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_hal():
    """Fresh MockHAL for each test."""
    return MockHAL()


@pytest.fixture
def bus(mock_hal):
    """Opened I2CBus backed by MockHAL."""
    b = I2CBus(hal=mock_hal)
    b.open()
    yield b
    b.close()


# ---------------------------------------------------------------------------
# I2CBus tests
# ---------------------------------------------------------------------------

class TestI2CBus:

    def test_context_manager_opens_and_closes(self, mock_hal):
        mock_hal.add_device(0x68)
        with I2CBus(hal=mock_hal) as bus:
            assert bus.is_open
        assert not bus.is_open

    def test_write_increments_stats(self, bus, mock_hal):
        mock_hal.add_device(0x68)
        bus.write(0x68, b"\x6B\x00")
        assert bus.stats.tx_bytes == 2
        assert bus.stats.transactions == 1

    def test_read_increments_stats(self, bus, mock_hal):
        mock_hal.add_device(0x68)
        bus.read(0x68, 4)
        assert bus.stats.rx_bytes == 4

    def test_invalid_address_raises(self, bus):
        with pytest.raises(AddressError):
            bus.write(0x00, b"\x00")   # reserved
        with pytest.raises(AddressError):
            bus.write(0x78, b"\x00")   # out of range

    def test_device_not_found_raises(self, bus):
        with pytest.raises(DeviceNotFoundError) as exc:
            bus.write(0x68, b"\x00")   # no device registered
        assert exc.value.address == 0x68

    def test_scan_returns_registered_devices(self, bus, mock_hal):
        mock_hal.add_device(0x68)
        mock_hal.add_device(0x76)
        found = bus.scan()
        assert 0x68 in found
        assert 0x76 in found

    def test_is_present_true_for_registered(self, bus, mock_hal):
        mock_hal.add_device(0x48)
        assert bus.is_present(0x48) is True

    def test_is_present_false_for_missing(self, bus):
        assert bus.is_present(0x48) is False

    def test_set_bits(self, bus, mock_hal):
        mock_hal.add_device(0x68)
        mock_hal.set_register(0x68, 0x6B, b"\x00")
        bus.set_bits(0x68, 0x6B, 0x40)
        written = mock_hal.get_written(0x68, 0x6B)
        assert written[0] & 0x40

    def test_clear_bits(self, bus, mock_hal):
        mock_hal.add_device(0x68)
        mock_hal.set_register(0x68, 0x6B, b"\xFF")
        bus.clear_bits(0x68, 0x6B, 0x40)
        written = mock_hal.get_written(0x68, 0x6B)
        assert not (written[0] & 0x40)

    def test_read_register_word_be(self, bus, mock_hal):
        mock_hal.add_device(0x48)
        mock_hal.set_register(0x48, 0x00, b"\x12\x34")
        val = bus.read_register_word_be(0x48, 0x00)
        assert val == 0x1234

    def test_read_register_word_le(self, bus, mock_hal):
        mock_hal.add_device(0x48)
        mock_hal.set_register(0x48, 0x00, b"\x34\x12")
        val = bus.read_register_word_le(0x48, 0x00)
        assert val == 0x1234

    def test_stats_reset(self, bus, mock_hal):
        mock_hal.add_device(0x68)
        bus.write(0x68, b"\x00")
        bus.stats.reset()
        assert bus.stats.tx_bytes == 0
        assert bus.stats.transactions == 0

    def test_repr(self, bus):
        r = repr(bus)
        assert "I2CBus" in r
        assert "MockHAL" in r


# ---------------------------------------------------------------------------
# BME280 tests
# ---------------------------------------------------------------------------

class TestBME280:

    def _make_bme280(self, hal, bus):
        """Set up mock registers for BME280 init."""
        hal.add_device(0x76)
        # Chip ID = 0x60
        hal.set_register(0x76, 0xD0, b"\x60")
        # Calibration data (26 + 7 bytes of zeros — simplified)
        hal.set_register(0x76, 0x88, bytes(26))
        hal.set_register(0x76, 0xE1, bytes(7))
        return BME280(bus, address=0x76)

    def test_init_success(self, bus, mock_hal):
        sensor = self._make_bme280(mock_hal, bus)
        assert sensor.address == 0x76

    def test_wrong_chip_id_raises(self, bus, mock_hal):
        mock_hal.add_device(0x76)
        mock_hal.set_register(0x76, 0xD0, b"\xFF")  # wrong ID
        mock_hal.set_register(0x76, 0x88, bytes(26))
        mock_hal.set_register(0x76, 0xE1, bytes(7))
        with pytest.raises(RuntimeError, match="chip ID"):
            BME280(bus, address=0x76)

    def test_reading_returns_dataclass(self, bus, mock_hal):
        sensor = self._make_bme280(mock_hal, bus)
        # Raw data = 8 zeros → all zero ADC values
        mock_hal.set_register(0x76, 0xF7, bytes(8))
        reading = sensor.read()
        assert hasattr(reading, "temperature_c")
        assert hasattr(reading, "pressure_hpa")
        assert hasattr(reading, "humidity_pct")
        assert isinstance(reading.temperature_c, float)

    def test_reading_str_format(self, bus, mock_hal):
        sensor = self._make_bme280(mock_hal, bus)
        mock_hal.set_register(0x76, 0xF7, bytes(8))
        reading = sensor.read()
        s = str(reading)
        assert "°C" in s
        assert "hPa" in s
        assert "%RH" in s

    def test_ping(self, bus, mock_hal):
        sensor = self._make_bme280(mock_hal, bus)
        assert sensor.ping() is True

    def test_repr(self, bus, mock_hal):
        sensor = self._make_bme280(mock_hal, bus)
        assert "BME280" in repr(sensor)
        assert "0x76" in repr(sensor)


# ---------------------------------------------------------------------------
# MPU6050 tests
# ---------------------------------------------------------------------------

class TestMPU6050:

    def _make_imu(self, hal, bus):
        hal.add_device(0x68)
        hal.set_register(0x68, 0x75, b"\x68")  # WHO_AM_I
        hal.set_register(0x68, 0x6B, b"\x00")  # PWR_MGMT_1
        return MPU6050(bus)

    def test_init_success(self, bus, mock_hal):
        imu = self._make_imu(mock_hal, bus)
        assert imu.address == 0x68

    def test_wrong_who_am_i_raises(self, bus, mock_hal):
        mock_hal.add_device(0x68)
        mock_hal.set_register(0x68, 0x75, b"\xAA")
        with pytest.raises(RuntimeError, match="WHO_AM_I"):
            MPU6050(bus)

    def test_read_returns_correct_types(self, bus, mock_hal):
        imu = self._make_imu(mock_hal, bus)
        # 14 bytes of zeros → all zero readings
        mock_hal.set_register(0x68, 0x3B, bytes(14))
        r = imu.read()
        assert isinstance(r.accel_x, float)
        assert isinstance(r.gyro_z, float)
        assert isinstance(r.temperature_c, float)

    def test_gravity_at_rest(self, bus, mock_hal):
        imu = self._make_imu(mock_hal, bus)
        # accel_z = 8192 (±4g range, 8192 LSB/g → 1.0g)
        raw = struct.pack(">7h", 0, 0, 8192, 0, 0, 0, 0)
        mock_hal.set_register(0x68, 0x3B, raw)
        r = imu.read()
        assert abs(r.accel_z - 1.0) < 0.001

    def test_temperature_formula(self, bus, mock_hal):
        imu = self._make_imu(mock_hal, bus)
        # temp_raw=0 → 0/340 + 36.53 = 36.53°C
        raw = struct.pack(">7h", 0, 0, 0, 0, 0, 0, 0)
        mock_hal.set_register(0x68, 0x3B, raw)
        r = imu.read()
        assert abs(r.temperature_c - 36.53) < 0.01

    def test_reading_str(self, bus, mock_hal):
        imu = self._make_imu(mock_hal, bus)
        mock_hal.set_register(0x68, 0x3B, bytes(14))
        r = imu.read()
        assert "Accel" in str(r)
        assert "Gyro" in str(r)


# ---------------------------------------------------------------------------
# DS3231 tests
# ---------------------------------------------------------------------------

class TestDS3231:

    def _make_rtc(self, hal, bus):
        hal.add_device(0x68)
        hal.set_register(0x68, 0x0E, b"\x1C")
        hal.set_register(0x68, 0x0F, b"\x00")
        return DS3231(bus)

    def test_init(self, bus, mock_hal):
        rtc = self._make_rtc(mock_hal, bus)
        assert rtc.address == 0x68

    def test_set_and_get_time(self, bus, mock_hal):
        rtc = self._make_rtc(mock_hal, bus)
        test_dt = datetime(2026, 3, 17, 14, 30, 0)
        rtc.set_time(test_dt)

        # Build the expected BCD bytes that set_time would write
        def b(v): return ((v // 10) << 4) | (v % 10)
        expected = bytes([b(0), b(30), b(14), 1, b(17), b(3), b(26)])
        mock_hal.set_register(0x68, 0x00, expected)

        got = rtc.get_time()
        assert got.year  == 2026
        assert got.month == 3
        assert got.day   == 17
        assert got.hour  == 14
        assert got.minute == 30

    def test_temperature_positive(self, bus, mock_hal):
        rtc = self._make_rtc(mock_hal, bus)
        # 25°C: MSB=25, LSB=0x00 (0.00 fractional)
        mock_hal.set_register(0x68, 0x11, bytes([25, 0x00]))
        assert rtc.read_temperature() == 25.0

    def test_temperature_fractional(self, bus, mock_hal):
        rtc = self._make_rtc(mock_hal, bus)
        # 25.25°C: MSB=25, LSB=0x40 (bits[7:6]=01 → 0.25)
        mock_hal.set_register(0x68, 0x11, bytes([25, 0x40]))
        assert rtc.read_temperature() == 25.25


# ---------------------------------------------------------------------------
# AT24C32 tests
# ---------------------------------------------------------------------------

class TestAT24C32:

    def _make_eeprom(self, hal, bus):
        hal.add_device(0x50)
        return AT24C32(bus)

    def test_init(self, bus, mock_hal):
        eeprom = self._make_eeprom(mock_hal, bus)
        assert eeprom.address == 0x50

    def test_write_records_in_log(self, bus, mock_hal):
        eeprom = self._make_eeprom(mock_hal, bus)
        eeprom.write(0x0000, b"\xDE\xAD\xBE\xEF")
        assert len(mock_hal.write_log) > 0

    def test_capacity_exceeded_raises(self, bus, mock_hal):
        eeprom = self._make_eeprom(mock_hal, bus)
        with pytest.raises(ValueError, match="capacity"):
            eeprom.write(4090, bytes(10))  # 4090+10 > 4096

    def test_read_capacity_exceeded_raises(self, bus, mock_hal):
        eeprom = self._make_eeprom(mock_hal, bus)
        with pytest.raises(ValueError, match="capacity"):
            eeprom.read(4090, 10)


# ---------------------------------------------------------------------------
# PCF8574 tests
# ---------------------------------------------------------------------------

class TestPCF8574:

    def _make_gpio(self, hal, bus):
        hal.add_device(0x20)
        return PCF8574(bus)

    def test_init_sets_all_high(self, bus, mock_hal):
        gpio = self._make_gpio(mock_hal, bus)
        # Last write should be 0xFF (all HIGH)
        last_write = mock_hal.write_log[-1]
        assert last_write[1][0] == 0xFF

    def test_set_pin_low(self, bus, mock_hal):
        gpio = self._make_gpio(mock_hal, bus)
        gpio.set_pin(0, False)
        last = mock_hal.write_log[-1]
        assert not (last[1][0] & 0x01)

    def test_set_pin_high(self, bus, mock_hal):
        gpio = self._make_gpio(mock_hal, bus)
        gpio.set_pin(0, False)
        gpio.set_pin(0, True)
        last = mock_hal.write_log[-1]
        assert last[1][0] & 0x01

    def test_invalid_pin_raises(self, bus, mock_hal):
        gpio = self._make_gpio(mock_hal, bus)
        with pytest.raises(ValueError):
            gpio.set_pin(8, True)

    def test_read_pins_length(self, bus, mock_hal):
        gpio = self._make_gpio(mock_hal, bus)
        mock_hal.set_register(0x20, 0, b"\xAA")
        pins = gpio.read_pins()
        assert len(pins) == 8


# ---------------------------------------------------------------------------
# SSD1306 tests
# ---------------------------------------------------------------------------

class TestSSD1306:

    def _make_display(self, hal, bus):
        hal.add_device(0x3C)
        return SSD1306(bus)

    def test_init(self, bus, mock_hal):
        d = self._make_display(mock_hal, bus)
        assert d.address == 0x3C

    def test_set_pixel_in_bounds(self, bus, mock_hal):
        d = self._make_display(mock_hal, bus)
        d.clear()
        d.set_pixel(0, 0, True)
        assert d._buf[0] & 0x01

    def test_set_pixel_out_of_bounds_ignored(self, bus, mock_hal):
        d = self._make_display(mock_hal, bus)
        d.clear()
        d.set_pixel(200, 200, True)  # silent ignore
        assert all(b == 0 for b in d._buf)

    def test_clear_zeroes_buffer(self, bus, mock_hal):
        d = self._make_display(mock_hal, bus)
        d.fill()
        d.clear()
        assert all(b == 0 for b in d._buf)

    def test_fill_sets_all_bits(self, bus, mock_hal):
        d = self._make_display(mock_hal, bus)
        d.fill()
        assert all(b == 0xFF for b in d._buf)

    def test_flush_sends_1024_bytes(self, bus, mock_hal):
        d = self._make_display(mock_hal, bus)
        d.flush()
        # Last write should contain 1025 bytes (1 ctrl + 1024 pixels)
        last = mock_hal.write_log[-1]
        assert len(last[1]) == 1025


# ---------------------------------------------------------------------------
# INA226 tests
# ---------------------------------------------------------------------------

class TestINA226:

    def _make_ina(self, hal, bus):
        hal.add_device(0x40)
        hal.set_register(0x40, 0xFE, b"\x54\x49")  # TI manufacturer ID
        hal.set_register(0x40, 0x02, b"\x00\x00")  # bus voltage
        hal.set_register(0x40, 0x04, b"\x00\x00")  # current
        hal.set_register(0x40, 0x03, b"\x00\x00")  # power
        return INA226(bus)

    def test_init(self, bus, mock_hal):
        ina = self._make_ina(mock_hal, bus)
        assert ina.address == 0x40

    def test_wrong_mfr_id_raises(self, bus, mock_hal):
        mock_hal.add_device(0x40)
        mock_hal.set_register(0x40, 0xFE, b"\xAB\xCD")
        with pytest.raises(RuntimeError):
            INA226(bus)

    def test_read_returns_dataclass(self, bus, mock_hal):
        ina = self._make_ina(mock_hal, bus)
        r = ina.read()
        assert hasattr(r, "voltage_v")
        assert hasattr(r, "current_ma")
        assert hasattr(r, "power_mw")

    def test_zero_reading(self, bus, mock_hal):
        ina = self._make_ina(mock_hal, bus)
        r = ina.read()
        assert r.voltage_v == 0.0
        assert r.current_ma == 0.0


# ---------------------------------------------------------------------------
# VEML7700 tests
# ---------------------------------------------------------------------------

class TestVEML7700:

    def _make_light(self, hal, bus):
        hal.add_device(0x10)
        hal.set_register(0x10, 0x00, b"\x00\x00")
        hal.set_register(0x10, 0x04, b"\x00\x00")
        return VEML7700(bus)

    def test_init(self, bus, mock_hal):
        s = self._make_light(mock_hal, bus)
        assert s.address == 0x10

    def test_read_lux_zero(self, bus, mock_hal):
        s = self._make_light(mock_hal, bus)
        mock_hal.set_register(0x10, 0x04, b"\x00\x00")
        assert s.read_lux() == 0.0

    def test_read_lux_returns_float(self, bus, mock_hal):
        s = self._make_light(mock_hal, bus)
        mock_hal.set_register(0x10, 0x04, b"\x00\x64")  # raw=100
        lux = s.read_lux()
        assert isinstance(lux, float)
        assert lux > 0
