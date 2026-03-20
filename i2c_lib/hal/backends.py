"""
i2c_lib.hal
===========
Hardware Abstraction Layer — platform backends.

IMPROVEMENT OVER C VERSION:
  C uses a struct of function pointers (i2c_hal_t) filled manually.
  Python uses abstract base classes (ABC) — subclass I2CHAL and
  implement the required methods. Python enforces the interface at
  class definition time, not at runtime.

  Available backends:
    LinuxI2CHAL     — Raspberry Pi, BeagleBone, any Linux /dev/i2c-N
    SMBusHAL        — smbus2 library (same Linux devices, richer API)
    CH341HAL        — USB-I2C adapter (CH341A chip, common on ebay)
    CP2112HAL       — USB-I2C adapter (Silicon Labs CP2112)
    FT232HHAL       — USB-I2C via FTDI FT232H (libftdi / pyftdi)
    MicroPythonHAL  — machine.I2C on ESP32, RP2040, STM32 with MicroPython
    MockHAL         — In-memory mock for unit tests (no hardware needed)
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from typing import Optional


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class I2CHAL(ABC):
    """Abstract I2C HAL.

    Subclass this and implement all abstract methods to port the library
    to a new platform.  All I2CBus operations call only these methods.

    Example — minimal custom HAL::

        class MyHAL(I2CHAL):
            def write(self, address, data): ...
            def read(self, address, length): ...
            def write_register(self, address, register, data): ...
            def read_register(self, address, register, length): ...
            def scan(self): ...
            def close(self): ...
    """

    @abstractmethod
    def write(self, address: int, data: bytes) -> None:
        """Send bytes to device at *address*.

        Args:
            address: 7-bit I2C device address (0x08–0x77).
            data:    Bytes to transmit.

        Raises:
            DeviceNotFoundError: If no ACK received for address.
            NACKError:           If data phase is NACKed.
            BusBusyError:        If bus is stuck.
        """

    @abstractmethod
    def read(self, address: int, length: int) -> bytes:
        """Receive *length* bytes from device at *address*.

        Args:
            address: 7-bit I2C device address.
            length:  Number of bytes to receive.

        Returns:
            bytes of length *length*.
        """

    @abstractmethod
    def write_register(self, address: int, register: int,
                        data: bytes) -> None:
        """Write *data* to *register* on device at *address*.

        Sends: START | ADDR W | REG | data... | STOP
        """

    @abstractmethod
    def read_register(self, address: int, register: int,
                       length: int) -> bytes:
        """Read *length* bytes from *register* on device at *address*.

        Sends: START | ADDR W | REG | RSTART | ADDR R | data... | STOP
        """

    @abstractmethod
    def scan(self) -> list[int]:
        """Probe all valid addresses and return list of responding ones.

        Returns:
            List of 7-bit addresses (0x08–0x77) that responded with ACK.
        """

    @abstractmethod
    def close(self) -> None:
        """Release hardware resources."""


# ---------------------------------------------------------------------------
# Linux /dev/i2c-N  (Raspberry Pi, BeagleBone, etc.)
# ---------------------------------------------------------------------------

class LinuxI2CHAL(I2CHAL):
    """Direct Linux I2C HAL using ``/dev/i2c-N`` via ``ioctl``.

    Works on any Linux SBC: Raspberry Pi, BeagleBone, Orange Pi, etc.
    Does NOT require smbus2 — uses only stdlib ``fcntl`` and ``ioctl``.

    Args:
        bus_number: I2C bus number (e.g. 1 for /dev/i2c-1 on Raspberry Pi).

    Example::

        with LinuxI2CHAL(1) as hal:
            hal.write(0x68, bytes([0x6B, 0x00]))  # wake MPU-6050

    Enabling I2C on Raspberry Pi::

        sudo raspi-config  →  Interface Options → I2C → Enable
        # Then reboot. /dev/i2c-1 will appear.
    """

    I2C_SLAVE = 0x0703
    I2C_SLAVE_FORCE = 0x0706
    I2C_RDWR = 0x0707
    I2C_M_RD = 0x0001

    def __init__(self, bus_number: int = 1):
        self._bus_number = bus_number
        self._fd: Optional[int] = None

    def open(self) -> "LinuxI2CHAL":
        import os
        self._fd = os.open(f"/dev/i2c-{self._bus_number}", os.O_RDWR)
        return self

    def close(self) -> None:
        import os
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()

    def _set_address(self, address: int) -> None:
        import fcntl
        from ..exceptions import DeviceNotFoundError
        try:
            fcntl.ioctl(self._fd, self.I2C_SLAVE, address)
        except OSError as e:
            raise DeviceNotFoundError(address) from e

    def write(self, address: int, data: bytes) -> None:
        import os
        from ..exceptions import DeviceNotFoundError, NACKError
        self._set_address(address)
        try:
            os.write(self._fd, data)
        except OSError as e:
            if e.errno == 6:
                raise DeviceNotFoundError(address) from e
            raise NACKError(str(e)) from e

    def read(self, address: int, length: int) -> bytes:
        import os
        from ..exceptions import DeviceNotFoundError
        self._set_address(address)
        try:
            return os.read(self._fd, length)
        except OSError as e:
            raise DeviceNotFoundError(address) from e

    def write_register(self, address: int, register: int,
                        data: bytes) -> None:
        self.write(address, bytes([register]) + data)

    def read_register(self, address: int, register: int,
                       length: int) -> bytes:
        self.write(address, bytes([register]))
        return self.read(address, length)

    def scan(self) -> list[int]:
        found = []
        for addr in range(0x08, 0x78):
            try:
                self.read(addr, 1)
                found.append(addr)
            except Exception:
                pass
        return found


# ---------------------------------------------------------------------------
# smbus2 HAL (Linux, richer API)
# ---------------------------------------------------------------------------

class SMBusHAL(I2CHAL):
    """I2C HAL using the ``smbus2`` library.

    ``smbus2`` provides a higher-level API over ``/dev/i2c-N`` and
    supports SMBus-specific operations (block read/write, PEC).

    Install::

        pip install smbus2

    Args:
        bus_number: I2C bus number.

    Example::

        with SMBusHAL(1) as hal:
            data = hal.read_register(0x68, 0x75, 1)  # WHO_AM_I
    """

    def __init__(self, bus_number: int = 1):
        self._bus_number = bus_number
        self._bus = None

    def open(self) -> "SMBusHAL":
        try:
            from smbus2 import SMBus
            self._bus = SMBus(self._bus_number)
        except ImportError as e:
            raise ImportError(
                "smbus2 not installed. Run: pip install smbus2"
            ) from e
        return self

    def close(self) -> None:
        if self._bus:
            self._bus.close()
            self._bus = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()

    def write(self, address: int, data: bytes) -> None:
        from smbus2 import i2c_msg
        msg = i2c_msg.write(address, list(data))
        self._bus.i2c_rdwr(msg)

    def read(self, address: int, length: int) -> bytes:
        from smbus2 import i2c_msg
        msg = i2c_msg.read(address, length)
        self._bus.i2c_rdwr(msg)
        return bytes(msg)

    def write_register(self, address: int, register: int,
                        data: bytes) -> None:
        self._bus.write_i2c_block_data(address, register, list(data))

    def read_register(self, address: int, register: int,
                       length: int) -> bytes:
        return bytes(
            self._bus.read_i2c_block_data(address, register, length)
        )

    def scan(self) -> list[int]:
        found = []
        for addr in range(0x08, 0x78):
            try:
                self._bus.read_byte(addr)
                found.append(addr)
            except Exception:
                pass
        return found


# ---------------------------------------------------------------------------
# MicroPython HAL  (ESP32, RP2040, STM32)
# ---------------------------------------------------------------------------

class MicroPythonHAL(I2CHAL):
    """I2C HAL for MicroPython targets.

    Uses ``machine.I2C`` which is available on ESP32, RP2040 (Pico),
    STM32, and most MicroPython-supported boards.

    Args:
        id:   Bus ID (0 or 1 on most boards).
        scl:  SCL pin number or ``machine.Pin`` object.
        sda:  SDA pin number or ``machine.Pin`` object.
        freq: Clock frequency in Hz (default 400000).

    Example (ESP32)::

        hal = MicroPythonHAL(id=0, scl=22, sda=21, freq=400_000)
        hal.open()

    Example (Raspberry Pi Pico)::

        hal = MicroPythonHAL(id=0, scl=9, sda=8)
        hal.open()
    """

    def __init__(self, id: int = 0, scl=None, sda=None,
                 freq: int = 400_000):
        self._id   = id
        self._scl  = scl
        self._sda  = sda
        self._freq = freq
        self._i2c  = None

    def open(self) -> "MicroPythonHAL":
        import machine  # type: ignore  # MicroPython built-in
        if self._scl is not None and self._sda is not None:
            scl = (machine.Pin(self._scl)
                   if isinstance(self._scl, int) else self._scl)
            sda = (machine.Pin(self._sda)
                   if isinstance(self._sda, int) else self._sda)
            self._i2c = machine.I2C(
                self._id, scl=scl, sda=sda, freq=self._freq
            )
        else:
            self._i2c = machine.I2C(self._id, freq=self._freq)
        return self

    def close(self) -> None:
        self._i2c = None

    def write(self, address: int, data: bytes) -> None:
        from ..exceptions import DeviceNotFoundError
        result = self._i2c.writeto(address, data)
        if result == 0:
            raise DeviceNotFoundError(address)

    def read(self, address: int, length: int) -> bytes:
        return bytes(self._i2c.readfrom(address, length))

    def write_register(self, address: int, register: int,
                        data: bytes) -> None:
        self._i2c.writeto_mem(address, register, data)

    def read_register(self, address: int, register: int,
                       length: int) -> bytes:
        return bytes(self._i2c.readfrom_mem(address, register, length))

    def scan(self) -> list[int]:
        return self._i2c.scan()


# ---------------------------------------------------------------------------
# CH341 USB-I2C adapter
# ---------------------------------------------------------------------------

class CH341HAL(I2CHAL):
    """I2C HAL for CH341A USB-to-I2C adapters.

    CH341A is a cheap (~$2) USB adapter that appears as /dev/ttyUSBx
    or a libusb device. Common on eBay/AliExpress as "USB logic analyser"
    or "USB programmer".

    Requires::

        pip install ch341a  # or: pip install pyusb

    Args:
        device_index: Index of CH341 device (0 = first).
    """

    def __init__(self, device_index: int = 0):
        self._index = device_index
        self._dev   = None

    def open(self) -> "CH341HAL":
        try:
            import ch341a  # type: ignore
            self._dev = ch341a.CH341A(self._index)
            self._dev.set_i2c_speed(ch341a.I2C_SPEED_FAST)
        except ImportError as e:
            raise ImportError(
                "ch341a not installed. Run: pip install ch341a"
            ) from e
        return self

    def close(self) -> None:
        if self._dev:
            self._dev.close()
            self._dev = None

    def write(self, address: int, data: bytes) -> None:
        self._dev.i2c_write(address, data)

    def read(self, address: int, length: int) -> bytes:
        return bytes(self._dev.i2c_read(address, length))

    def write_register(self, address: int, register: int,
                        data: bytes) -> None:
        self._dev.i2c_write(address, bytes([register]) + data)

    def read_register(self, address: int, register: int,
                       length: int) -> bytes:
        self._dev.i2c_write(address, bytes([register]))
        return bytes(self._dev.i2c_read(address, length))

    def scan(self) -> list[int]:
        found = []
        for addr in range(0x08, 0x78):
            try:
                self._dev.i2c_read(addr, 1)
                found.append(addr)
            except Exception:
                pass
        return found


# ---------------------------------------------------------------------------
# Mock HAL — for unit tests (no hardware needed)
# ---------------------------------------------------------------------------

class MockHAL(I2CHAL):
    """In-memory mock HAL for unit tests.

    IMPROVEMENT OVER C VERSION:
      C tests require real hardware or complex GPIO mocking.
      Python's MockHAL runs completely in RAM — no MCU, no wires.
      Tests run on any machine (CI/CD, laptop, GitHub Actions).

    The mock holds a register map per device address.
    Tests pre-populate register values, then verify reads/writes.

    Example::

        hal = MockHAL()
        hal.set_register(0x68, 0x75, b'\\x68')  # WHO_AM_I = 0x68

        imu = MPU6050(I2CBus(hal=hal))
        assert imu.who_am_i() == 0x68

    Attributes:
        write_log:  List of (address, data) tuples — all writes recorded.
        read_log:   List of (address, length) tuples — all reads recorded.
    """

    def __init__(self):
        # register_map[address][register] = bytes
        self._registers: dict[int, dict[int, bytes]] = {}
        self.write_log:  list[tuple[int, bytes]]      = []
        self.read_log:   list[tuple[int, int]]         = []
        self._devices:   set[int]                      = set()

    # --- Setup helpers (call these in tests) ---

    def add_device(self, address: int) -> None:
        """Register a device address so scan() finds it."""
        self._devices.add(address)
        if address not in self._registers:
            self._registers[address] = {}

    def set_register(self, address: int, register: int,
                     value: bytes) -> None:
        """Pre-populate a register value for read operations."""
        if address not in self._registers:
            self._registers[address] = {}
        self._registers[address][register] = value

    def get_written(self, address: int, register: int) -> bytes:
        """Retrieve what was last written to a register (for assertions)."""
        return self._registers.get(address, {}).get(register, b"")

    # --- I2CHAL interface ---

    def open(self) -> "MockHAL":
        return self

    def close(self) -> None:
        pass

    def write(self, address: int, data: bytes) -> None:
        from ..exceptions import DeviceNotFoundError
        if address not in self._devices:
            raise DeviceNotFoundError(address)
        self.write_log.append((address, data))
        if len(data) >= 2:
            reg, val = data[0], data[1:]
            if address not in self._registers:
                self._registers[address] = {}
            self._registers[address][reg] = val

    def read(self, address: int, length: int) -> bytes:
        from ..exceptions import DeviceNotFoundError
        if address not in self._devices:
            raise DeviceNotFoundError(address)
        self.read_log.append((address, length))
        return bytes(length)

    def write_register(self, address: int, register: int,
                        data: bytes) -> None:
        self.write(address, bytes([register]) + data)

    def read_register(self, address: int, register: int,
                       length: int) -> bytes:
        from ..exceptions import DeviceNotFoundError
        if address not in self._devices:
            raise DeviceNotFoundError(address)
        self.read_log.append((address, length))
        val = self._registers.get(address, {}).get(register, bytes(length))
        return val[:length].ljust(length, b"\x00")

    def scan(self) -> list[int]:
        return sorted(self._devices)
