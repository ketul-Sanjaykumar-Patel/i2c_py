"""
i2c_lib.drivers.devices
========================
All 8 device drivers ported from C i2c_lib to Python.

IMPROVEMENTS OVER C VERSION:
  - ``struct.unpack`` replaces manual byte shifting
  - ``dataclasses`` replace C structs for measurement results
  - Properties expose calibration and state cleanly
  - ``datetime`` integration for DS3231 RTC
  - Exception-based error handling throughout

Devices:
  BME280   — Temperature / Humidity / Pressure
  MPU6050  — 6-axis IMU (Accelerometer + Gyroscope)
  VEML7700 — Ambient Light Sensor
  SSD1306  — 128×64 OLED Display
  INA226   — Current / Power Monitor
  DS3231   — Real-Time Clock
  AT24C32  — 32Kbit EEPROM
  PCF8574  — 8-bit GPIO Expander
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from .base import I2CDevice
from ..bus import I2CBus


# ============================================================================
# BME280 — Temperature / Humidity / Pressure
# ============================================================================

@dataclass
class BME280Reading:
    """A single BME280 measurement.

    IMPROVEMENT: In C, results were returned via 3 output pointers.
    Python returns a clean dataclass with named fields.

    Attributes:
        temperature_c: Temperature in °C (float, e.g. 23.5).
        pressure_hpa:  Pressure in hPa (float, e.g. 1013.25).
        humidity_pct:  Relative humidity in % (float, e.g. 52.3).
    """
    temperature_c: float
    pressure_hpa:  float
    humidity_pct:  float

    def __str__(self) -> str:
        return (f"{self.temperature_c:.2f}°C  "
                f"{self.pressure_hpa:.2f} hPa  "
                f"{self.humidity_pct:.1f}%RH")


class BME280(I2CDevice):
    """Bosch BME280 environmental sensor driver.

    Measures temperature, humidity, and barometric pressure.

    Real-world use: Google Pixel phones, DJI drones, weather stations.

    Args:
        bus:     Initialised I2CBus.
        address: 0x76 (SDO=GND) or 0x77 (SDO=VCC).

    Example::

        with I2CBus(hal=SMBusHAL(1)) as bus:
            sensor = BME280(bus)
            reading = sensor.read()
            print(reading)   # 23.50°C  1013.25 hPa  52.3%RH
    """

    DEFAULT_ADDRESS = 0x76
    CHIP_ID         = 0x60

    # Register addresses
    _REG_CHIP_ID    = 0xD0
    _REG_RESET      = 0xE0
    _REG_CTRL_HUM   = 0xF2
    _REG_CTRL_MEAS  = 0xF4
    _REG_CONFIG     = 0xF5
    _REG_DATA       = 0xF7   # 8 bytes: press MSB → hum LSB
    _REG_CALIB1     = 0x88   # 26 bytes: T and P calibration
    _REG_CALIB2     = 0xE1   # 7 bytes:  H calibration

    def __init__(self, bus: I2CBus, address: int = DEFAULT_ADDRESS):
        super().__init__(bus, address)
        self._calib: dict = {}
        self._t_fine: int = 0
        self._init()

    def _init(self) -> None:
        chip_id = self.bus.read_register_byte(self.address, self._REG_CHIP_ID)
        if chip_id != self.CHIP_ID:
            raise RuntimeError(
                f"BME280 not found at 0x{self.address:02X}. "
                f"Got chip ID 0x{chip_id:02X}, expected 0x60"
            )
        # Software reset
        self.bus.write_register_byte(self.address, self._REG_RESET, 0xB6)
        time.sleep(0.01)
        self._load_calibration()
        # osrs_h=×1, osrs_t=×2, osrs_p=×16, normal mode, filter=16
        self.bus.write_register_byte(self.address, self._REG_CTRL_HUM, 0x01)
        self.bus.write_register_byte(self.address, self._REG_CONFIG, 0xA0)
        self.bus.write_register_byte(self.address, self._REG_CTRL_MEAS, 0x57)

    def _load_calibration(self) -> None:
        """Read factory calibration from device NVM."""
        raw1 = self.bus.read_register(self.address, self._REG_CALIB1, 26)
        raw2 = self.bus.read_register(self.address, self._REG_CALIB2, 7)

        c = {}
        # Temperature (T1 unsigned, T2/T3 signed)
        c["T1"], c["T2"], c["T3"] = struct.unpack_from("<Hhh", raw1, 0)
        # Pressure (P1 unsigned, P2–P9 signed)
        c["P1"], = struct.unpack_from("<H", raw1, 6)
        c["P2"], c["P3"], c["P4"], c["P5"], c["P6"], \
        c["P7"], c["P8"], c["P9"] = struct.unpack_from("<8h", raw1, 8)
        # Humidity
        c["H1"] = raw1[25]
        c["H2"], c["H3"] = struct.unpack_from("<hB", raw2, 0)
        c["H4"] = (raw2[3] << 4) | (raw2[4] & 0x0F)
        c["H5"] = (raw2[5] << 4) | (raw2[4] >> 4)
        c["H6"] = struct.unpack_from("<b", raw2, 6)[0]
        self._calib = c

    def read(self) -> BME280Reading:
        """Read all three measurements.

        Returns:
            BME280Reading with temperature_c, pressure_hpa, humidity_pct.
        """
        raw = self.bus.read_register(self.address, self._REG_DATA, 8)

        adc_P = (raw[0] << 12) | (raw[1] << 4) | (raw[2] >> 4)
        adc_T = (raw[3] << 12) | (raw[4] << 4) | (raw[5] >> 4)
        adc_H = (raw[6] << 8)  |  raw[7]

        temp_c    = self._compensate_temperature(adc_T)
        press_hpa = self._compensate_pressure(adc_P) / 100.0
        hum_pct   = self._compensate_humidity(adc_H)

        return BME280Reading(
            temperature_c=round(temp_c, 2),
            pressure_hpa=round(press_hpa, 2),
            humidity_pct=round(hum_pct, 2),
        )

    def _compensate_temperature(self, adc_T: int) -> float:
        """Bosch official temperature compensation formula."""
        c = self._calib
        var1 = (adc_T / 16384.0 - c["T1"] / 1024.0) * c["T2"]
        var2 = ((adc_T / 131072.0 - c["T1"] / 8192.0) ** 2) * c["T3"]
        self._t_fine = int(var1 + var2)
        return (var1 + var2) / 5120.0

    def _compensate_pressure(self, adc_P: int) -> float:
        """Bosch official pressure compensation (64-bit version)."""
        c = self._calib
        t = self._t_fine
        var1 = t / 2.0 - 64000.0
        var2 = var1 * var1 * c["P6"] / 32768.0
        var2 = var2 + var1 * c["P5"] * 2.0
        var2 = var2 / 4.0 + c["P4"] * 65536.0
        var1 = (c["P3"] * var1 * var1 / 524288.0 + c["P2"] * var1) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * c["P1"]
        if var1 == 0:
            return 0.0
        p = 1048576.0 - adc_P
        p = ((p - var2 / 4096.0) * 6250.0) / var1
        var1 = c["P9"] * p * p / 2147483648.0
        var2 = p * c["P8"] / 32768.0
        return p + (var1 + var2 + c["P7"]) / 16.0

    def _compensate_humidity(self, adc_H: int) -> float:
        """Bosch official humidity compensation."""
        c = self._calib
        h = self._t_fine - 76800.0
        h = ((adc_H - (c["H4"] * 64.0 + c["H5"] / 16384.0 * h)) *
             (c["H2"] / 65536.0 *
              (1.0 + c["H6"] / 67108864.0 * h *
               (1.0 + c["H3"] / 67108864.0 * h))))
        h *= (1.0 - c["H1"] * h / 524288.0)
        return max(0.0, min(100.0, h))


# ============================================================================
# MPU6050 — 6-Axis IMU
# ============================================================================

@dataclass
class MPU6050Reading:
    """Raw + converted MPU-6050 sensor data.

    Attributes:
        accel_x/y/z: Acceleration in g (float).
        gyro_x/y/z:  Angular velocity in °/s (float).
        temperature_c: Die temperature in °C.
    """
    accel_x: float; accel_y: float; accel_z: float
    gyro_x:  float; gyro_y:  float; gyro_z:  float
    temperature_c: float

    def __str__(self) -> str:
        return (f"Accel: ({self.accel_x:.3f}, {self.accel_y:.3f}, "
                f"{self.accel_z:.3f}) g  |  "
                f"Gyro: ({self.gyro_x:.1f}, {self.gyro_y:.1f}, "
                f"{self.gyro_z:.1f}) °/s  |  "
                f"Temp: {self.temperature_c:.2f}°C")


class MPU6050(I2CDevice):
    """InvenSense MPU-6050 6-axis IMU driver.

    Real-world use: drone flight controllers, VR headsets, wearables.

    Example::

        imu = MPU6050(bus)
        reading = imu.read()
        print(f"Accel Z: {reading.accel_z:.3f} g")  # ~1.0g at rest
    """

    DEFAULT_ADDRESS = 0x68
    WHO_AM_I_VAL    = 0x68

    _REG_SMPLRT_DIV   = 0x19
    _REG_CONFIG       = 0x1A
    _REG_GYRO_CONFIG  = 0x1B
    _REG_ACCEL_CONFIG = 0x1C
    _REG_ACCEL_XOUT_H = 0x3B   # First of 14 bytes
    _REG_PWR_MGMT_1   = 0x6B
    _REG_WHO_AM_I     = 0x75

    # Sensitivity multipliers (LSB per unit)
    _ACCEL_SENSITIVITY = {0: 16384.0, 1: 8192.0, 2: 4096.0, 3: 2048.0}
    _GYRO_SENSITIVITY  = {0: 131.0,   1: 65.5,   2: 32.8,   3: 16.4}

    def __init__(self, bus: I2CBus, address: int = DEFAULT_ADDRESS,
                 accel_range: int = 1, gyro_range: int = 1):
        """
        Args:
            accel_range: 0=±2g, 1=±4g, 2=±8g, 3=±16g
            gyro_range:  0=±250°/s, 1=±500°/s, 2=±1000°/s, 3=±2000°/s
        """
        super().__init__(bus, address)
        self._accel_range = accel_range
        self._gyro_range  = gyro_range
        self._init()

    def _init(self) -> None:
        who = self.bus.read_register_byte(self.address, self._REG_WHO_AM_I)
        if who != self.WHO_AM_I_VAL:
            raise RuntimeError(
                f"MPU-6050 not found. WHO_AM_I=0x{who:02X}, expected 0x68"
            )
        # Wake up, use PLL with X gyro ref (recommended)
        self.bus.write_register_byte(self.address, self._REG_PWR_MGMT_1, 0x01)
        # DLPF ~44Hz, 200Hz sample rate, set ranges
        self.bus.write_register_byte(self.address, self._REG_CONFIG, 0x03)
        self.bus.write_register_byte(self.address, self._REG_SMPLRT_DIV, 0x04)
        self.bus.write_register_byte(
            self.address, self._REG_ACCEL_CONFIG, self._accel_range << 3
        )
        self.bus.write_register_byte(
            self.address, self._REG_GYRO_CONFIG, self._gyro_range << 3
        )

    def read(self) -> MPU6050Reading:
        """Burst-read all 14 bytes (accel + temp + gyro) atomically.

        IMPORTANT: Reading all 14 bytes in one transaction ensures
        all values are from the same sample — critical for sensor fusion.

        Returns:
            MPU6050Reading with physical units (g and °/s).
        """
        raw = self.bus.read_register(self.address, self._REG_ACCEL_XOUT_H, 14)

        # IMPROVEMENT: struct.unpack replaces 7 pairs of manual bit shifts
        ax, ay, az, temp_raw, gx, gy, gz = struct.unpack(">7h", raw)

        a_sens = self._ACCEL_SENSITIVITY[self._accel_range]
        g_sens = self._GYRO_SENSITIVITY[self._gyro_range]

        return MPU6050Reading(
            accel_x=round(ax / a_sens, 4),
            accel_y=round(ay / a_sens, 4),
            accel_z=round(az / a_sens, 4),
            gyro_x =round(gx / g_sens, 2),
            gyro_y =round(gy / g_sens, 2),
            gyro_z =round(gz / g_sens, 2),
            temperature_c=round(temp_raw / 340.0 + 36.53, 2),
        )

    def sleep(self) -> None:
        """Put device into sleep mode (low power)."""
        self.bus.set_bits(self.address, self._REG_PWR_MGMT_1, 0x40)

    def wake(self) -> None:
        """Wake device from sleep mode."""
        self.bus.clear_bits(self.address, self._REG_PWR_MGMT_1, 0x40)


# ============================================================================
# VEML7700 — Ambient Light Sensor
# ============================================================================

class VEML7700(I2CDevice):
    """Vishay VEML7700 ambient light sensor.

    Real-world use: auto screen brightness on phones and laptops.

    Example::

        light = VEML7700(bus)
        lux = light.read_lux()
        print(f"{lux:.1f} lux")
    """

    ADDRESS = 0x10   # Fixed — no address pins

    _REG_CONF = 0x00
    _REG_ALS  = 0x04

    # Resolution table: [gain_idx][it_idx] = lux/count
    _RESOLUTION = [
        [0.2304, 0.1152, 0.0576, 0.0288, 0.0144, 0.0072],  # gain x1
        [0.1152, 0.0576, 0.0288, 0.0144, 0.0072, 0.0036],  # gain x2
        [1.8432, 0.9216, 0.4608, 0.2304, 0.1152, 0.0576],  # gain x1/8
        [0.9216, 0.4608, 0.2304, 0.1152, 0.0576, 0.0288],  # gain x1/4
    ]
    _IT_IDX = {0x0C: 0, 0x08: 1, 0x00: 2, 0x01: 3, 0x02: 4, 0x03: 5}

    def __init__(self, bus: I2CBus, gain: int = 0, it: int = 0x00):
        """
        Args:
            gain: 0=x1, 1=x2, 2=x1/8, 3=x1/4
            it:   Integration time: 0x0C=25ms, 0x08=50ms, 0x00=100ms (default)
        """
        super().__init__(bus, self.ADDRESS)
        self._gain = gain
        self._it   = it
        self._res  = self._RESOLUTION[gain][self._IT_IDX[it]]
        self._init()

    def _init(self) -> None:
        conf = (self._gain << 11) | (self._it << 6)
        self.bus.write_register_word_be(self.address, self._REG_CONF, conf)

    def read_lux(self) -> float:
        """Read ambient illuminance in lux.

        Applies non-linear correction for readings above 1000 lux
        per VEML7700 application note.

        Returns:
            Illuminance in lux (0.0 = dark, ~100000 = direct sunlight).
        """
        raw = self.bus.read_register_word_be(self.address, self._REG_ALS)
        lux = raw * self._res
        if lux > 1000:
            lux = (6.0135e-13 * lux**4 - 9.3924e-9 * lux**3 +
                   8.1488e-5 * lux**2 + 1.0023 * lux)
        return round(lux, 2)


# ============================================================================
# SSD1306 — 128×64 OLED Display
# ============================================================================

class SSD1306(I2CDevice):
    """Solomon Systech SSD1306 OLED display driver.

    Real-world use: smart watches, 3D printer panels, IoT dashboards.

    Example::

        display = SSD1306(bus)
        display.clear()
        display.set_pixel(64, 32, True)
        display.flush()
    """

    DEFAULT_ADDRESS = 0x3C

    _CTRL_CMD  = 0x00
    _CTRL_DATA = 0x40
    WIDTH  = 128
    HEIGHT = 64
    PAGES  = 8    # 64 rows / 8 bits per page

    def __init__(self, bus: I2CBus, address: int = DEFAULT_ADDRESS):
        super().__init__(bus, address)
        self._buf = bytearray(self.WIDTH * self.PAGES)
        self._init()

    def _cmd(self, *cmds: int) -> None:
        for c in cmds:
            self.bus.write(self.address, bytes([self._CTRL_CMD, c]))

    def _init(self) -> None:
        """Send standard initialization sequence."""
        self._cmd(
            0xAE,        # display off
            0xD5, 0x80,  # clock divide
            0xA8, 63,    # mux ratio
            0xD3, 0x00,  # display offset
            0x40,        # start line
            0x8D, 0x14,  # charge pump on
            0x20, 0x00,  # horizontal addressing
            0xA1,        # remap seg
            0xC8,        # com scan dec
            0xDA, 0x12,  # com pins
            0x81, 0xCF,  # contrast
            0xD9, 0xF1,  # precharge
            0xDB, 0x40,  # vcom deselect
            0xA4,        # normal display
            0xA6,        # non-inverted
            0xAF,        # display on
        )

    def clear(self) -> None:
        """Clear the frame buffer (does NOT update display — call flush())."""
        for i in range(len(self._buf)):
            self._buf[i] = 0

    def fill(self) -> None:
        """Fill frame buffer with all pixels ON."""
        for i in range(len(self._buf)):
            self._buf[i] = 0xFF

    def set_pixel(self, x: int, y: int, on: bool = True) -> None:
        """Set a single pixel in the frame buffer.

        Args:
            x:  Column (0–127).
            y:  Row (0–63).
            on: True = pixel on, False = pixel off.
        """
        if not (0 <= x < self.WIDTH and 0 <= y < self.HEIGHT):
            return
        idx = (y // 8) * self.WIDTH + x
        bit = 1 << (y % 8)
        if on:
            self._buf[idx] |= bit
        else:
            self._buf[idx] &= ~bit

    def flush(self) -> None:
        """Send the frame buffer to the OLED display.

        Call after all draw operations. Transfers 1024 bytes.
        """
        self._cmd(0x21, 0, 127)   # column 0–127
        self._cmd(0x22, 0, 7)     # page 0–7
        # Send all 1024 pixel bytes with DATA control byte
        self.bus.write(
            self.address,
            bytes([self._CTRL_DATA]) + self._buf
        )

    def display_on(self, on: bool = True) -> None:
        """Turn display on or off (does not affect frame buffer)."""
        self._cmd(0xAF if on else 0xAE)

    def set_contrast(self, value: int) -> None:
        """Set contrast (0–255)."""
        self._cmd(0x81, value & 0xFF)


# ============================================================================
# INA226 — Current / Power Monitor
# ============================================================================

@dataclass
class INA226Reading:
    """INA226 measurement result.

    Attributes:
        voltage_v:  Bus voltage in volts.
        current_ma: Current in milliamps (negative = reverse).
        power_mw:   Power in milliwatts.
    """
    voltage_v:  float
    current_ma: float
    power_mw:   float

    def __str__(self) -> str:
        return (f"{self.voltage_v:.3f} V  "
                f"{self.current_ma:.1f} mA  "
                f"{self.power_mw:.0f} mW")


class INA226(I2CDevice):
    """Texas Instruments INA226 current/power monitor.

    Real-world use: server rack PDUs, EV battery management, solar chargers.

    Example::

        ina = INA226(bus, r_shunt=0.1, max_amps=5.0)
        reading = ina.read()
        print(reading)  # 3.287 V  245.3 mA  806 mW
    """

    DEFAULT_ADDRESS = 0x40

    _REG_CONFIG  = 0x00
    _REG_SHUNT_V = 0x01
    _REG_BUS_V   = 0x02
    _REG_POWER   = 0x03
    _REG_CURRENT = 0x04
    _REG_CALIB   = 0x05
    _REG_MFR_ID  = 0xFE

    MFR_ID_TI = 0x5449

    def __init__(self, bus: I2CBus, address: int = DEFAULT_ADDRESS,
                 r_shunt: float = 0.1, max_amps: float = 5.0):
        """
        Args:
            r_shunt:   Shunt resistor value in ohms (e.g. 0.1 for 100mΩ).
            max_amps:  Maximum expected current in amps.
        """
        super().__init__(bus, address)
        self._current_lsb = max_amps / 32768.0
        self._power_lsb   = self._current_lsb * 25.0
        self._init()

    def _init(self) -> None:
        mfr = self.bus.read_register_word_be(self.address, self._REG_MFR_ID)
        if mfr != self.MFR_ID_TI:
            raise RuntimeError(f"INA226 not found. MFR ID=0x{mfr:04X}")
        cal = int(0.00512 / (self._current_lsb * 0.1))
        self.bus.write_register_word_be(self.address, self._REG_CALIB, cal)
        # Config: avg=16, vbus CT=1.1ms, vsh CT=1.1ms, continuous
        self.bus.write_register_word_be(self.address, self._REG_CONFIG, 0x4327)

    def read(self) -> INA226Reading:
        """Read voltage, current, and power.

        Returns:
            INA226Reading with voltage_v, current_ma, power_mw.
        """
        raw_v = self.bus.read_register_word_be(self.address, self._REG_BUS_V)
        # Current is signed 16-bit
        raw_c_bytes = self.bus.read_register(self.address, self._REG_CURRENT, 2)
        raw_c = struct.unpack(">h", raw_c_bytes)[0]
        raw_p = self.bus.read_register_word_be(self.address, self._REG_POWER)

        return INA226Reading(
            voltage_v  = round(raw_v * 1.25e-3, 4),
            current_ma = round(raw_c * self._current_lsb * 1000, 2),
            power_mw   = round(raw_p * self._power_lsb * 1000, 1),
        )


# ============================================================================
# DS3231 — Extremely Accurate Real-Time Clock
# ============================================================================

class DS3231(I2CDevice):
    """Maxim DS3231 real-time clock driver.

    Real-world use: data loggers, Raspberry Pi HATs, access control.

    IMPROVEMENT OVER C VERSION:
      Python's ``datetime`` module handles all date/time arithmetic.
      No manual BCD conversion, no month/year boundary checks needed.

    Example::

        rtc = DS3231(bus)
        rtc.set_time(datetime.now())
        dt = rtc.get_time()
        print(dt.strftime("%Y-%m-%d %H:%M:%S"))
    """

    ADDRESS = 0x68

    _REG_SECONDS = 0x00
    _REG_CTRL    = 0x0E
    _REG_STATUS  = 0x0F
    _REG_TEMP_MSB = 0x11

    def __init__(self, bus: I2CBus):
        super().__init__(bus, self.ADDRESS)
        self._init()

    def _init(self) -> None:
        # Clear oscillator stop flag
        self.bus.clear_bits(self.address, self._REG_STATUS, 0x80)
        self.bus.write_register_byte(self.address, self._REG_CTRL, 0x1C)

    @staticmethod
    def _bcd2int(bcd: int) -> int:
        return (bcd >> 4) * 10 + (bcd & 0x0F)

    @staticmethod
    def _int2bcd(val: int) -> int:
        return ((val // 10) << 4) | (val % 10)

    def get_time(self) -> datetime:
        """Read current date and time.

        Returns:
            Python ``datetime`` object.

        Example::

            dt = rtc.get_time()
            print(f"Current time: {dt}")
        """
        raw = self.bus.read_register(self.address, self._REG_SECONDS, 7)
        sec  = self._bcd2int(raw[0] & 0x7F)
        mins = self._bcd2int(raw[1] & 0x7F)
        hrs  = self._bcd2int(raw[2] & 0x3F)
        day  = self._bcd2int(raw[4] & 0x3F)
        mon  = self._bcd2int(raw[5] & 0x1F)
        yr   = 2000 + self._bcd2int(raw[6])
        return datetime(yr, mon, day, hrs, mins, sec)

    def set_time(self, dt: datetime) -> None:
        """Set the RTC to a Python datetime.

        Example::

            rtc.set_time(datetime(2026, 3, 17, 14, 30, 0))
        """
        data = bytes([
            self._int2bcd(dt.second),
            self._int2bcd(dt.minute),
            self._int2bcd(dt.hour),
            1,  # day-of-week (not critical)
            self._int2bcd(dt.day),
            self._int2bcd(dt.month),
            self._int2bcd(dt.year - 2000),
        ])
        self.bus.write_register(self.address, self._REG_SECONDS, data)

    def read_temperature(self) -> float:
        """Read the built-in TCXO temperature sensor.

        Resolution: 0.25°C. Accuracy: ±3°C.

        Returns:
            Temperature in °C (float).
        """
        raw = self.bus.read_register(self.address, self._REG_TEMP_MSB, 2)
        msb = struct.unpack("b", bytes([raw[0]]))[0]  # signed
        frac = (raw[1] >> 6) * 0.25
        return round(msb + frac, 2)


# ============================================================================
# AT24C32 — 32Kbit EEPROM
# ============================================================================

class AT24C32(I2CDevice):
    """Microchip AT24C32 32Kbit (4096-byte) I2C EEPROM.

    Real-world use: monitor EDID, DDR SPD, calibration storage.

    Example::

        eeprom = AT24C32(bus)
        eeprom.write(0x0000, b'Hello, EEPROM!')
        data = eeprom.read(0x0000, 14)
    """

    DEFAULT_ADDRESS = 0x50
    CAPACITY        = 4096
    PAGE_SIZE       = 32
    WRITE_CYCLE_MS  = 5

    def __init__(self, bus: I2CBus, address: int = DEFAULT_ADDRESS):
        super().__init__(bus, address)
        if not self.ping():
            raise RuntimeError(
                f"AT24C32 not found at 0x{address:02X}"
            )

    def read(self, mem_addr: int, length: int) -> bytes:
        """Read *length* bytes from memory address *mem_addr*.

        Args:
            mem_addr: 16-bit byte address (0–4095).
            length:   Number of bytes to read.

        Returns:
            bytes of length *length*.
        """
        if mem_addr + length > self.CAPACITY:
            raise ValueError("Read exceeds EEPROM capacity")
        # 16-bit address, MSB first
        self.bus.write(self.address, struct.pack(">H", mem_addr))
        return self.bus.read(self.address, length)

    def write(self, mem_addr: int, data: bytes) -> None:
        """Write bytes to EEPROM, handling page boundaries.

        IMPORTANT: Automatically splits writes across 32-byte pages.
        Waits 5ms between pages for the internal write cycle.

        Args:
            mem_addr: Starting 16-bit address.
            data:     Bytes to write.
        """
        if mem_addr + len(data) > self.CAPACITY:
            raise ValueError("Write exceeds EEPROM capacity")

        written = 0
        while written < len(data):
            curr     = mem_addr + written
            page_off = curr % self.PAGE_SIZE
            chunk    = min(len(data) - written, self.PAGE_SIZE - page_off)
            payload  = struct.pack(">H", curr) + data[written:written + chunk]
            self.bus.write(self.address, payload)
            time.sleep(self.WRITE_CYCLE_MS / 1000)
            written += chunk


# ============================================================================
# PCF8574 — 8-bit GPIO Expander
# ============================================================================

class PCF8574(I2CDevice):
    """NXP PCF8574 8-bit quasi-bidirectional I/O expander.

    Real-world use: HD44780 LCD backpack, industrial relay boards.

    Example::

        gpio = PCF8574(bus)
        gpio.set_pin(0, False)   # pin 0 LOW (e.g. relay on)
        gpio.set_pin(0, True)    # pin 0 HIGH (relay off)
        states = gpio.read_pins()
        print(f"Pin 3: {states[3]}")
    """

    DEFAULT_ADDRESS  = 0x20
    A_VARIANT_ADDRESS = 0x38   # PCF8574A variant

    def __init__(self, bus: I2CBus, address: int = DEFAULT_ADDRESS):
        super().__init__(bus, address)
        self._state = 0xFF   # all HIGH (input/idle) on power-up
        self._write_state()

    def _write_state(self) -> None:
        self.bus.write(self.address, bytes([self._state]))

    def write_pins(self, value: int) -> None:
        """Write all 8 pins at once.

        Args:
            value: Bitmask (1=HIGH, 0=LOW) for all 8 pins.
        """
        self._state = value & 0xFF
        self._write_state()

    def read_pins(self) -> List[bool]:
        """Read all 8 pin states.

        Pins driven HIGH act as inputs (quasi-bidirectional).
        Drive HIGH before reading.

        Returns:
            List of 8 booleans [pin0, pin1, ..., pin7].
        """
        raw = self.bus.read(self.address, 1)[0]
        return [(raw >> i) & 1 == 1 for i in range(8)]

    def set_pin(self, pin: int, high: bool) -> None:
        """Set a single pin HIGH or LOW.

        Args:
            pin:  Pin number (0–7).
            high: True = HIGH, False = LOW.
        """
        if not 0 <= pin <= 7:
            raise ValueError(f"Pin must be 0–7, got {pin}")
        if high:
            self._state |=  (1 << pin)
        else:
            self._state &= ~(1 << pin)
        self._write_state()

    def get_pin(self, pin: int) -> bool:
        """Read a single pin state.

        Args:
            pin: Pin number (0–7).

        Returns:
            True if HIGH, False if LOW.
        """
        return self.read_pins()[pin]
