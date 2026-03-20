"""
i2c_lib.bus
===========
Core I2CBus class — the main entry point for all I2C communication.

PYTHONIC IMPROVEMENTS OVER C VERSION:
  1. Context manager (``with`` statement) — bus auto-closes on exit,
     even if an exception occurs. No resource leaks.
  2. Type hints throughout — IDEs show autocomplete and type errors.
  3. ``struct`` module for byte packing — replaces manual bit shifting.
  4. Properties for bus state — clean attribute access.
  5. ``__repr__`` for easy debugging.
  6. Dataclass for BusStats — no manual struct definition.

Usage::

    # Raspberry Pi
    from i2c_lib import I2CBus
    from i2c_lib.hal import SMBusHAL

    with I2CBus(hal=SMBusHAL(1)) as bus:
        bus.write(0x68, bytes([0x6B, 0x00]))     # wake MPU-6050
        data = bus.read_register(0x68, 0x3B, 14) # read accel+gyro
        devices = bus.scan()
        print(f"Found: {[hex(d) for d in devices]}")
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field
from typing import Optional

from .exceptions import AddressError, I2CError
from .hal.backends import I2CHAL

# Valid 7-bit address range per I2C spec
_ADDR_MIN = 0x08
_ADDR_MAX = 0x77


# ---------------------------------------------------------------------------
# Bus statistics — dataclass replaces C struct
# ---------------------------------------------------------------------------

@dataclass
class BusStats:
    """Accumulated I2C bus statistics.

    IMPROVEMENT OVER C VERSION:
      ``@dataclass`` auto-generates ``__init__``, ``__repr__``,
      and ``__eq__``. In C this was a manually written struct.

    Attributes:
        tx_bytes:          Total bytes written to devices.
        rx_bytes:          Total bytes read from devices.
        transactions:      Number of completed transactions.
        errors:            Total errors encountered.
        nack_count:        Address NACKs (device not found).
        scan_count:        Number of bus scans performed.
    """
    tx_bytes:     int = field(default=0)
    rx_bytes:     int = field(default=0)
    transactions: int = field(default=0)
    errors:       int = field(default=0)
    nack_count:   int = field(default=0)
    scan_count:   int = field(default=0)

    def reset(self) -> None:
        """Reset all counters to zero."""
        self.tx_bytes = self.rx_bytes = self.transactions = 0
        self.errors = self.nack_count = self.scan_count = 0


# ---------------------------------------------------------------------------
# I2CBus
# ---------------------------------------------------------------------------

class I2CBus:
    """Main I2C bus interface.

    Wraps any I2CHAL backend and provides a consistent, Pythonic API
    that mirrors the C i2c_lib while adding Python idioms.

    Args:
        hal:        HAL backend (SMBusHAL, LinuxI2CHAL, MicroPythonHAL,
                    CH341HAL, or MockHAL for tests).
        timeout_ms: Default operation timeout in milliseconds.

    Context manager usage (recommended)::

        with I2CBus(hal=SMBusHAL(1)) as bus:
            bus.write_register(0x68, 0x6B, b'\\x00')

    Manual usage (remember to call close)::

        bus = I2CBus(hal=SMBusHAL(1))
        bus.open()
        try:
            bus.write(0x68, b'\\x6B\\x00')
        finally:
            bus.close()
    """

    def __init__(self, hal: I2CHAL, timeout_ms: int = 100):
        self._hal        = hal
        self._timeout_ms = timeout_ms
        self._stats      = BusStats()
        self._open       = False

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "I2CBus":
        """Open the bus. Called automatically by ``with`` statement."""
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close bus — called even if an exception occurred."""
        self.close()
        return False  # do not suppress exceptions

    def open(self) -> "I2CBus":
        """Manually open the bus (prefer ``with`` statement instead)."""
        if hasattr(self._hal, "open"):
            self._hal.open()
        self._open = True
        return self

    def close(self) -> None:
        """Release bus resources."""
        if self._open:
            self._hal.close()
            self._open = False

    # ------------------------------------------------------------------
    # Core write / read
    # ------------------------------------------------------------------

    def write(self, address: int, data: bytes) -> None:
        """Write bytes directly to a device (no register address).

        Args:
            address: 7-bit device address.
            data:    Bytes to send.

        Raises:
            DeviceNotFoundError: No ACK on address.
            NACKError:           Device NACKed the data.
            AddressError:        Address outside 0x08–0x77.

        Example::

            # Send two bytes: register 0x6B, value 0x00
            bus.write(0x68, bytes([0x6B, 0x00]))
        """
        self._check_address(address)
        try:
            self._hal.write(address, data)
            self._stats.tx_bytes += len(data)
            self._stats.transactions += 1
        except I2CError as e:
            self._stats.errors += 1
            raise

    def read(self, address: int, length: int) -> bytes:
        """Read bytes directly from a device (no register address).

        Args:
            address: 7-bit device address.
            length:  Number of bytes to receive.

        Returns:
            ``bytes`` of length *length*.

        Example::

            raw = bus.read(0x68, 6)  # read 6 bytes
        """
        self._check_address(address)
        try:
            data = self._hal.read(address, length)
            self._stats.rx_bytes += len(data)
            self._stats.transactions += 1
            return data
        except I2CError as e:
            self._stats.errors += 1
            raise

    # ------------------------------------------------------------------
    # Register operations
    # ------------------------------------------------------------------

    def write_register(self, address: int, register: int,
                        data: bytes) -> None:
        """Write *data* bytes starting at *register*.

        Most common I2C operation for configuring sensors.

        Args:
            address:  7-bit device address.
            register: Register address (1 byte).
            data:     Data bytes to write.

        Example::

            # Set MPU-6050 PWR_MGMT_1 (0x6B) to 0x00 (wake up)
            bus.write_register(0x68, 0x6B, b'\\x00')
        """
        self._check_address(address)
        try:
            self._hal.write_register(address, register, data)
            self._stats.tx_bytes += 1 + len(data)
            self._stats.transactions += 1
        except I2CError:
            self._stats.errors += 1
            raise

    def read_register(self, address: int, register: int,
                       length: int) -> bytes:
        """Read *length* bytes from *register*.

        Uses repeated START (write register, then read data) without
        releasing the bus between phases.

        Args:
            address:  7-bit device address.
            register: Register address to read from.
            length:   Number of bytes to read.

        Returns:
            ``bytes`` of length *length*.

        Example::

            # Read WHO_AM_I register from MPU-6050 (should be 0x68)
            data = bus.read_register(0x68, 0x75, 1)
            assert data[0] == 0x68
        """
        self._check_address(address)
        try:
            data = self._hal.read_register(address, register, length)
            self._stats.tx_bytes += 1
            self._stats.rx_bytes += len(data)
            self._stats.transactions += 1
            return data
        except I2CError:
            self._stats.errors += 1
            raise

    # ------------------------------------------------------------------
    # Typed register helpers
    # IMPROVEMENT OVER C VERSION: struct.pack/unpack replaces manual
    # bit shifting. Clean, readable, and handles endianness correctly.
    # ------------------------------------------------------------------

    def read_register_byte(self, address: int, register: int) -> int:
        """Read a single unsigned byte from a register.

        Returns:
            Integer 0–255.

        Example::

            chip_id = bus.read_register_byte(0x76, 0xD0)  # BME280 chip ID
        """
        return self.read_register(address, register, 1)[0]

    def write_register_byte(self, address: int, register: int,
                             value: int) -> None:
        """Write a single byte to a register.

        Example::

            bus.write_register_byte(0x68, 0x6B, 0x00)  # wake MPU-6050
        """
        self.write_register(address, register, bytes([value & 0xFF]))

    def read_register_word_be(self, address: int, register: int) -> int:
        """Read a 16-bit big-endian unsigned word from two registers.

        Returns:
            Integer 0–65535.

        Example::

            # ADS1115 conversion result (big-endian)
            raw = bus.read_register_word_be(0x48, 0x00)
        """
        data = self.read_register(address, register, 2)
        return struct.unpack(">H", data)[0]

    def read_register_word_le(self, address: int, register: int) -> int:
        """Read a 16-bit little-endian unsigned word.

        Example::

            # STMicro sensors use little-endian
            raw = bus.read_register_word_le(0x19, 0x28)
        """
        data = self.read_register(address, register, 2)
        return struct.unpack("<H", data)[0]

    def write_register_word_be(self, address: int, register: int,
                                value: int) -> None:
        """Write a 16-bit big-endian word to a register pair."""
        self.write_register(address, register,
                             struct.pack(">H", value & 0xFFFF))

    # ------------------------------------------------------------------
    # Bit manipulation
    # IMPROVEMENT OVER C VERSION: Python int operations are cleaner
    # than C unsigned arithmetic — no overflow, no casting needed.
    # ------------------------------------------------------------------

    def set_bits(self, address: int, register: int, mask: int) -> None:
        """Set specific bits in a register (read-modify-write).

        Args:
            mask: Bits set to 1 will be set in the register.

        Example::

            # Enable interrupt pin on MPU-6050 (bit 0 of 0x38)
            bus.set_bits(0x68, 0x38, 0x01)
        """
        val = self.read_register_byte(address, register)
        self.write_register_byte(address, register, val | mask)

    def clear_bits(self, address: int, register: int, mask: int) -> None:
        """Clear specific bits in a register (read-modify-write).

        Example::

            # Put MPU-6050 to sleep (bit 6 of PWR_MGMT_1 = 0x6B)
            bus.clear_bits(0x68, 0x6B, 0x40)
        """
        val = self.read_register_byte(address, register)
        self.write_register_byte(address, register, val & ~mask)

    def update_bits(self, address: int, register: int,
                    mask: int, value: int) -> None:
        """Update a bit field in a register (read-modify-write).

        Args:
            mask:  Field mask (e.g. 0x18 for bits [4:3]).
            value: New field value (pre-shifted to correct position).

        Example::

            # Set MPU-6050 accel range to ±4g (bits [4:3] of 0x1C)
            bus.update_bits(0x68, 0x1C, mask=0x18, value=0x08)
        """
        val = self.read_register_byte(address, register)
        self.write_register_byte(address, register,
                                 (val & ~mask) | (value & mask))

    # ------------------------------------------------------------------
    # Bus diagnostics
    # ------------------------------------------------------------------

    def scan(self) -> list[int]:
        """Probe all valid addresses (0x08–0x77) for responding devices.

        Returns:
            Sorted list of 7-bit addresses that responded with ACK.

        Example::

            devices = bus.scan()
            for addr in devices:
                print(f"  Found device at 0x{addr:02X}")
        """
        self._stats.scan_count += 1
        return self._hal.scan()

    def is_present(self, address: int) -> bool:
        """Check if a device is present at *address*.

        Returns:
            True if device responded, False if not found.

        Example::

            if not bus.is_present(0x68):
                raise RuntimeError("MPU-6050 not connected!")
        """
        self._check_address(address)
        try:
            self._hal.read(address, 1)
            return True
        except I2CError:
            return False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def stats(self) -> BusStats:
        """Accumulated bus statistics (read-only)."""
        return self._stats

    @property
    def is_open(self) -> bool:
        """True if the bus is currently open."""
        return self._open

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_address(self, address: int) -> None:
        """Validate 7-bit address range."""
        if not (_ADDR_MIN <= address <= _ADDR_MAX):
            raise AddressError(address)

    def __repr__(self) -> str:
        status = "open" if self._open else "closed"
        return (f"I2CBus(hal={self._hal.__class__.__name__}, "
                f"status={status}, "
                f"transactions={self._stats.transactions})")
