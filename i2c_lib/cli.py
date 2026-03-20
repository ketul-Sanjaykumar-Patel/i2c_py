"""
i2c_lib.cli
===========
Command-line tools for I2C bus interaction.

After ``pip install i2c-lib``, these commands become available:

    i2c scan [bus]            — scan bus for devices
    i2c read  <addr> <reg> <len> [bus]  — read registers
    i2c write <addr> <reg> <hex>  [bus] — write register
    i2c dump  <addr> [bus]        — dump first 256 registers
    i2c info                  — show library version + platform
"""

from __future__ import annotations

import argparse
import sys

BANNER = "i2c_lib CLI v1.0.0"


def _get_hal(bus_num: int):
    """Auto-detect best available HAL for the current platform."""
    # Try smbus2 first (richer API), fall back to raw Linux
    try:
        from i2c_lib.hal.backends import SMBusHAL
        return SMBusHAL(bus_num)
    except ImportError:
        pass
    try:
        from i2c_lib.hal.backends import LinuxI2CHAL
        return LinuxI2CHAL(bus_num)
    except Exception:
        pass
    print("ERROR: No I2C HAL available. Install smbus2: pip install smbus2",
          file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------

def cmd_scan(args) -> None:
    """Scan the I2C bus and print a formatted device map."""
    from i2c_lib.bus import I2CBus

    print(f"\n{BANNER}")
    print(f"Scanning I2C bus {args.bus} (/dev/i2c-{args.bus})...\n")

    KNOWN = {
        0x10: "VEML7700 (light)",
        0x20: "PCF8574 (GPIO)",
        0x3C: "SSD1306 OLED",     0x3D: "SSD1306 OLED",
        0x40: "INA226/INA219",
        0x48: "ADS1115/TMP102",
        0x50: "AT24C32 EEPROM",
        0x57: "DS3231 EEPROM",
        0x60: "MPL3115A2",
        0x68: "DS3231/MPU-6050",  0x69: "MPU-6050 (AD0=1)",
        0x76: "BME280",           0x77: "BME280/BMP280",
    }

    with I2CBus(hal=_get_hal(args.bus)) as bus:
        found = bus.scan()

    if not found:
        print("No devices found. Check wiring and pull-up resistors!")
        return

    print(f"     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f")
    for row in range(8):
        base = row * 16
        line = f"  {base:02x}:"
        for col in range(16):
            addr = base + col
            if addr < 0x08 or addr > 0x77:
                line += " --"
            elif addr in found:
                line += f" {addr:02x}"
            else:
                line += " .."
        print(line)

    print(f"\nFound {len(found)} device(s):")
    for addr in found:
        name = KNOWN.get(addr, "Unknown")
        print(f"  0x{addr:02X}  —  {name}")
    print()


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

def cmd_read(args) -> None:
    """Read bytes from a register."""
    from i2c_lib.bus import I2CBus

    addr = int(args.address, 16)
    reg  = int(args.register, 16)

    with I2CBus(hal=_get_hal(args.bus)) as bus:
        data = bus.read_register(addr, reg, args.length)

    hex_str  = " ".join(f"{b:02X}" for b in data)
    dec_str  = " ".join(str(b) for b in data)
    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)

    print(f"\nDevice: 0x{addr:02X}  Register: 0x{reg:02X}  Length: {args.length}")
    print(f"  HEX  : {hex_str}")
    print(f"  DEC  : {dec_str}")
    print(f"  ASCII: {ascii_str}\n")


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def cmd_write(args) -> None:
    """Write bytes to a register."""
    from i2c_lib.bus import I2CBus

    addr   = int(args.address, 16)
    reg    = int(args.register, 16)
    data   = bytes.fromhex(args.data.replace(" ", ""))

    with I2CBus(hal=_get_hal(args.bus)) as bus:
        bus.write_register(addr, reg, data)

    hex_str = " ".join(f"{b:02X}" for b in data)
    print(f"\nWrote {len(data)} byte(s) to 0x{addr:02X} reg 0x{reg:02X}: {hex_str}\n")


# ---------------------------------------------------------------------------
# dump
# ---------------------------------------------------------------------------

def cmd_dump(args) -> None:
    """Dump the first 256 registers of a device."""
    from i2c_lib.bus import I2CBus

    addr = int(args.address, 16)
    print(f"\nRegister dump: device 0x{addr:02X} on bus {args.bus}")
    print("     00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F")

    with I2CBus(hal=_get_hal(args.bus)) as bus:
        for row in range(16):
            base = row * 16
            try:
                chunk = bus.read_register(addr, base, 16)
                hex_row = " ".join(f"{b:02X}" for b in chunk)
                print(f"  {base:02X}: {hex_row}")
            except Exception:
                print(f"  {base:02X}: -- -- -- -- -- -- -- -- "
                      "-- -- -- -- -- -- -- --")
    print()


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------

def cmd_info(args) -> None:
    """Show library and platform info."""
    import platform
    from i2c_lib import __version__

    print(f"\n{BANNER}")
    print(f"  Library version : {__version__}")
    print(f"  Python version  : {sys.version.split()[0]}")
    print(f"  Platform        : {platform.system()} {platform.machine()}")

    # Check available HALs
    hals = []
    try:
        import smbus2; hals.append("smbus2 (installed)")
    except ImportError:
        hals.append("smbus2 (not installed — pip install smbus2)")
    try:
        import machine; hals.append("MicroPython machine.I2C")  # type: ignore
    except ImportError:
        pass

    print(f"  Available HALs  : {', '.join(hals)}")
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="i2c",
        description="i2c_lib command-line tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  i2c scan                        # scan bus 1 (default)
  i2c scan 0                      # scan bus 0
  i2c read  0x68 0x75 1           # read WHO_AM_I from MPU-6050
  i2c write 0x68 0x6B 00          # wake MPU-6050
  i2c dump  0x68                  # dump all registers
  i2c info                        # show platform info
        """,
    )
    parser.add_argument("--version", action="version", version="1.0.0")
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Scan bus for devices")
    p_scan.add_argument("bus", nargs="?", type=int, default=1,
                         help="I2C bus number (default: 1)")
    p_scan.set_defaults(func=cmd_scan)

    # read
    p_read = sub.add_parser("read", help="Read register bytes")
    p_read.add_argument("address",  help="Device address (hex, e.g. 0x68)")
    p_read.add_argument("register", help="Register address (hex, e.g. 0x75)")
    p_read.add_argument("length", type=int, help="Number of bytes to read")
    p_read.add_argument("bus", nargs="?", type=int, default=1)
    p_read.set_defaults(func=cmd_read)

    # write
    p_write = sub.add_parser("write", help="Write register bytes")
    p_write.add_argument("address",  help="Device address (hex)")
    p_write.add_argument("register", help="Register address (hex)")
    p_write.add_argument("data",     help="Hex bytes to write (e.g. '00 01 FF')")
    p_write.add_argument("bus", nargs="?", type=int, default=1)
    p_write.set_defaults(func=cmd_write)

    # dump
    p_dump = sub.add_parser("dump", help="Dump device registers 0x00–0xFF")
    p_dump.add_argument("address", help="Device address (hex)")
    p_dump.add_argument("bus", nargs="?", type=int, default=1)
    p_dump.set_defaults(func=cmd_dump)

    # info
    p_info = sub.add_parser("info", help="Show library and platform info")
    p_info.set_defaults(func=cmd_info)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
