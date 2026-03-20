"""
i2c_lib — Universal I2C Driver Library for Python
==================================================

Quick start::

    from i2c_lib import I2CBus
    from i2c_lib.hal import SMBusHAL
    from i2c_lib.drivers import BME280

    with I2CBus(hal=SMBusHAL(1)) as bus:
        sensor = BME280(bus)
        reading = sensor.read()
        print(reading)
"""

__version__ = "1.0.0"
__author__  = "Ketul Sanjaykumar Patel"

from .bus import I2CBus, BusStats
from .exceptions import (
    I2CError, DeviceNotFoundError, NACKError,
    BusBusyError, TimeoutError, AddressError,
    ClockStretchError, ArbitrationLostError,
)

__all__ = [
    "I2CBus", "BusStats",
    "I2CError", "DeviceNotFoundError", "NACKError",
    "BusBusyError", "TimeoutError", "AddressError",
    "ClockStretchError", "ArbitrationLostError",
]
