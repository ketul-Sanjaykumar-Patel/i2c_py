"""
i2c_lib.exceptions
==================
All exceptions raised by the library.

IMPROVEMENT OVER C VERSION:
  C uses integer return codes (I2C_OK = 0, I2C_ERR_NACK = -1, etc.)
  Python uses exceptions — errors propagate automatically up the call
  stack without requiring every caller to check a return value.

  Exception hierarchy:
    I2CError                   ← base for all i2c_lib errors
    ├── BusNotInitializedError  ← forgot to call __enter__ / init
    ├── DeviceNotFoundError     ← NACK on address (no device)
    ├── BusIOError              ← NACK on data, bus stuck
    │   ├── NACKError
    │   └── BusBusyError
    ├── TimeoutError            ← operation took too long
    ├── ClockStretchError       ← slave held SCL too long
    └── ArbitrationLostError    ← multi-master conflict
"""


class I2CError(OSError):
    """Base class for all i2c_lib errors.

    Inherits from OSError so it integrates naturally with Python's
    I/O error handling and can be caught by ``except OSError``.
    """


class BusNotInitializedError(I2CError):
    """Raised when a bus method is called before initialization.

    Example::

        bus = I2CBus(1)
        bus.write(0x68, b'\\x00')   # raises BusNotInitializedError
        # must use: with I2CBus(1) as bus: ...
    """


class DeviceNotFoundError(I2CError):
    """Raised when no ACK is received for the device address.

    This means either:
    - The device is not connected
    - The device address is wrong
    - The device is powered off
    - Pull-up resistors are missing

    Attributes:
        address (int): The 7-bit I2C address that did not respond.
    """

    def __init__(self, address: int, message: str = ""):
        self.address = address
        super().__init__(
            message or f"No device found at address 0x{address:02X}"
        )


class NACKError(I2CError):
    """Raised when a device sends NACK during a data transfer.

    Different from DeviceNotFoundError — the device was addressed
    successfully but rejected the data (e.g. register address out of
    range, device busy with internal operation).
    """


class BusBusyError(I2CError):
    """Raised when SDA or SCL is stuck LOW.

    Usually means a previous transaction was interrupted.
    Call ``bus.recover()`` to attempt recovery.
    """


class TimeoutError(I2CError):  # noqa: A001  (shadows built-in intentionally)
    """Raised when an operation exceeds the configured timeout."""


class ClockStretchError(I2CError):
    """Raised when a slave holds SCL LOW longer than allowed.

    Some slow devices (e.g. certain EEPROMs mid-write) hold SCL to
    pause the master. This error fires if they hold it too long.
    """


class ArbitrationLostError(I2CError):
    """Raised in multi-master configurations when arbitration is lost.

    Another master took control of the bus mid-transaction.
    The transaction should be retried.
    """


class AddressError(I2CError):
    """Raised when an address is outside the valid 7-bit range (0x08–0x77).

    Attributes:
        address (int): The invalid address that was provided.
    """

    def __init__(self, address: int):
        self.address = address
        super().__init__(
            f"Address 0x{address:02X} is outside valid range 0x08–0x77"
        )
