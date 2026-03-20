# i2c_py — Universal I2C Driver Library for Python

[![Build](https://github.com/ketul-Sanjaykumar-Patel/i2c_py/actions/workflows/build.yml/badge.svg)](https://github.com/ketul-Sanjaykumar-Patel/i2c_py/actions/workflows/build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-52%20passing-brightgreen.svg)](tests/)

Python port of [i2c_lib](https://github.com/ketul-Sanjaykumar-Patel/i2c_lib) — the C I2C driver library.
Supports Raspberry Pi, USB-I2C adapters, MicroPython, and unit testing without hardware.

---

## Features

- **Context manager API** — `with I2CBus(...) as bus:` auto-closes on exit
- **Full exception hierarchy** — `DeviceNotFoundError`, `NACKError`, `BusBusyError`
- **Type hints throughout** — IDE autocomplete and type checking
- **Multiple platform backends** — Linux, smbus2, MicroPython, CH341, CP2112
- **MockHAL for testing** — 52 pytest tests, zero hardware needed
- **8 device drivers** — same devices as C library
- **CLI tool** — `i2c scan`, `i2c read`, `i2c write`, `i2c dump`
- **pip installable** — `pip install i2c-lib`

---

## Supported Platforms

| Platform | HAL Class | Install |
|----------|-----------|---------|
| Raspberry Pi / Linux | `SMBusHAL` | `pip install smbus2` |
| Any Linux `/dev/i2c-N` | `LinuxI2CHAL` | No extra deps |
| MicroPython (ESP32, RP2040) | `MicroPythonHAL` | Built-in `machine.I2C` |
| USB adapter CH341A | `CH341HAL` | `pip install ch341a` |
| Unit tests (no hardware) | `MockHAL` | Built-in |

---

## Supported Devices

| Device | Category | Real-world use |
|--------|----------|----------------|
| BME280 | Temp / Humidity / Pressure | Google Pixel, DJI drones |
| MPU-6050 | 6-axis IMU | Drones, VR headsets, wearables |
| VEML7700 | Ambient light | Auto screen brightness |
| SSD1306 | 128×64 OLED | Smart watches, 3D printers |
| INA226 | Current / Power monitor | Server PDUs, EVs |
| DS3231 | Real-time clock | Data loggers, Raspberry Pi HATs |
| AT24C32 | 32Kbit EEPROM | Config storage, calibration |
| PCF8574 | 8-bit GPIO expander | LCD backpack, relay boards |

---

## Quick Start

```bash
pip install smbus2
pip install .
```

```python
from i2c_lib import I2CBus
from i2c_lib.hal import SMBusHAL
from i2c_lib.drivers import BME280, MPU6050

# Raspberry Pi — bus 1
with I2CBus(hal=SMBusHAL(1)) as bus:

    # Scan for devices
    devices = bus.scan()
    print([hex(d) for d in devices])   # ['0x68', '0x76']

    # BME280 — temperature, humidity, pressure
    sensor = BME280(bus)
    reading = sensor.read()
    print(reading)   # 23.50°C  1013.25 hPa  52.3%RH

    # MPU-6050 — accelerometer + gyroscope
    imu = MPU6050(bus)
    data = imu.read()
    print(f"Accel Z: {data.accel_z:.3f} g")   # ~1.000 g at rest
```

---

## CLI Tool

```bash
# Scan bus 1 for all devices
i2c scan

# Read WHO_AM_I register from MPU-6050
i2c read 0x68 0x75 1

# Wake up MPU-6050 (write 0x00 to register 0x6B)
i2c write 0x68 0x6B 00

# Dump all registers of a device
i2c dump 0x68

# Show platform and library info
i2c info
```

---

## MicroPython (ESP32 / RP2040)

```python
from i2c_lib import I2CBus
from i2c_lib.hal import MicroPythonHAL
from i2c_lib.drivers import BME280

# ESP32: SDA=GPIO21, SCL=GPIO22
hal = MicroPythonHAL(id=0, scl=22, sda=21, freq=400_000)
hal.open()

with I2CBus(hal=hal) as bus:
    sensor = BME280(bus)
    print(sensor.read())
```

---

## File Structure

```
i2c_py/
├── i2c_lib/
│   ├── __init__.py          ← package entry, exports I2CBus + exceptions
│   ├── bus.py               ← I2CBus class (context manager, full API)
│   ├── exceptions.py        ← exception hierarchy
│   ├── cli.py               ← i2c scan/read/write/dump/info
│   ├── hal/
│   │   ├── __init__.py
│   │   └── backends.py      ← LinuxI2CHAL, SMBusHAL, MicroPythonHAL,
│   │                           CH341HAL, MockHAL
│   └── drivers/
│       ├── __init__.py
│       ├── base.py          ← I2CDevice base class
│       └── devices.py       ← all 8 device drivers
├── tests/
│   └── test_all.py          ← 52 pytest tests, no hardware needed
└── pyproject.toml           ← pip install i2c-lib
```

---

## Running Tests

```bash
pip install pytest
pytest tests/ -v
# 52 passed in 0.26s — no hardware required
```

---

## Python vs C — Key Differences

| C i2c_lib | Python i2c_py |
|-----------|---------------|
| `i2c_err_t` return codes | Exceptions (`DeviceNotFoundError`) |
| `i2c_hal_t` function pointers | `I2CHAL` abstract base class |
| Manual bit shifting | `struct.unpack(">H", data)` |
| Output pointer params | Return dataclasses (`BME280Reading`) |
| No tests | 52 pytest tests with MockHAL |
| Copy `.c/.h` files | `pip install i2c-lib` |
| No CLI | `i2c scan`, `i2c read 0x68 0x75 1` |

---

## License

MIT — see [LICENSE](LICENSE).

## Related

- [i2c_lib](https://github.com/ketul-Sanjaykumar-Patel/i2c_lib) — C version
- [spi_lib](https://github.com/ketul-Sanjaykumar-Patel/spi_lib) — SPI driver in C
