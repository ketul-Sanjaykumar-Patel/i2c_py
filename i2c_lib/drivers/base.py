"""
i2c_lib.drivers.base
====================
Abstract base class for all device drivers.

IMPROVEMENT OVER C VERSION:
  C drivers are standalone functions taking a struct pointer.
  Python drivers are classes — they hold state, validate on init,
  and provide a clean interface via inheritance.

  Every driver inherits from I2CDevice which provides:
    - Address validation
    - Bus access via self.bus
    - __repr__ for debugging
    - ping() to check device presence
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from ..bus import I2CBus


class I2CDevice(ABC):
    """Abstract base class for all I2C device drivers.

    Args:
        bus:     Initialised I2CBus instance.
        address: 7-bit I2C address of this device.

    Subclass example::

        class MyDevice(I2CDevice):
            DEFAULT_ADDRESS = 0x48

            def __init__(self, bus, address=DEFAULT_ADDRESS):
                super().__init__(bus, address)
                self._init_device()

            def _init_device(self):
                chip_id = self.bus.read_register_byte(self.address, 0xD0)
                if chip_id != 0x60:
                    raise RuntimeError(f"Unexpected chip ID: {chip_id:#x}")
    """

    def __init__(self, bus: I2CBus, address: int):
        self._bus     = bus
        self._address = address

    @property
    def bus(self) -> I2CBus:
        """The I2CBus this device is attached to."""
        return self._bus

    @property
    def address(self) -> int:
        """7-bit I2C address of this device."""
        return self._address

    def ping(self) -> bool:
        """Check if the device is responding on the bus.

        Returns:
            True if device is present and responding.
        """
        return self._bus.is_present(self._address)

    def __repr__(self) -> str:
        return (f"{self.__class__.__name__}("
                f"address=0x{self._address:02X})")