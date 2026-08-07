#!/usr/bin/env python3
"""Patch an MTK preloader image for DA bypass.

Supports zeroing the AND_ROMINFO_v block, which causes the Download Agent
to fall back to permissive mode, enabling unrestricted partition writes
via SP Flash Tool or mtkclient.

After patching, re-sign with preloader-resign before flashing.
"""

import argparse
import os
import shutil
import struct
import sys


# AND_ROMINFO_v offset from file start for UFS_BOOT preloaders
AND_ROMINFO_OFFSET_UFS = 0x1288
AND_ROMINFO_SIZE = 16
AND_ROMINFO_MAGIC = b'AND_ROMINFO_v'


def detect_container(data):
    if data[0:8] == b'UFS_BOOT':
        return 'UFS_BOOT'
    if data[0:9] == b'EMMC_BOOT':
        return 'EMMC_BOOT'
    if data[0:3] == b'MMM':
        return 'GFH_BARE'
    return 'UNKNOWN'


def find_rominfo(data):
    """Search for AND_ROMINFO_v magic and return its offset, or None if not found."""
    idx = data.find(AND_ROMINFO_MAGIC)
    if idx < 0:
        return None
    return idx


def get_brlyt_gfh_offset(data):
    """Get the GFH offset from the BRLYT boot_region_addr field."""
    brlyt_off = data.find(b'BRLYT')
    if brlyt_off < 0:
        return None
    return struct.unpack_from('<I', data, brlyt_off + 0x0c)[0]


def cmd_info(args, data):
    container = detect_container(data)
    print(f"File:       {args.image}")
    print(f"Size:       {len(data)} bytes (0x{len(data):x})")
    print(f"Container:  {container}")

    gfh_offset = None
    if container == 'UFS_BOOT':
        gfh_offset = get_brlyt_gfh_offset(data)
        if gfh_offset:
            print(f"GFH offset: 0x{gfh_offset:x}")

    rominfo_off = find_rominfo(data)
    if rominfo_off is not None:
        rominfo_bytes = data[rominfo_off:rominfo_off + AND_ROMINFO_SIZE]
        print(f"\nAND_ROMINFO_v found at offset 0x{rominfo_off:x}:")
        print(f"  Bytes: {rominfo_bytes.hex()}")
        print(f"  Magic: {rominfo_bytes[:len(AND_ROMINFO_MAGIC)]}")
        print(f"  Status: PRESENT (DA security policy active)")
    else:
        print(f"\nAND_ROMINFO_v: NOT FOUND (already zeroed or different firmware)")
        print(f"  Expected at: 0x{AND_ROMINFO_OFFSET_UFS:x} (UFS_BOOT)")
        # Double-check the expected offset
        at_expected = data[AND_ROMINFO_OFFSET_UFS:AND_ROMINFO_OFFSET_UFS + AND_ROMINFO_SIZE]
        print(f"  Bytes at 0x{AND_ROMINFO_OFFSET_UFS:x}: {at_expected.hex()}")


def cmd_zero_rominfo(args, data):
    data = bytearray(data)

    # Try to find the magic first
    rominfo_off = find_rominfo(data)
    if rominfo_off is not None:
        print(f"Found AND_ROMINFO_v at 0x{rominfo_off:x}")
    else:
        # Fall back to known offset for UFS_BOOT
        container = detect_container(data)
        if container == 'UFS_BOOT':
            rominfo_off = AND_ROMINFO_OFFSET_UFS
            print(f"AND_ROMINFO_v magic not found, using default UFS_BOOT offset 0x{rominfo_off:x}")
            at_offset = bytes(data[rominfo_off:rominfo_off + AND_ROMINFO_SIZE])
            print(f"Bytes at offset: {at_offset.hex()}")
        else:
            print(f"Error: AND_ROMINFO_v not found and container type is '{container}' (not UFS_BOOT)", file=sys.stderr)
            print("Use --offset to specify the offset manually.", file=sys.stderr)
            sys.exit(1)

    if args.offset is not None:
        rominfo_off = args.offset
        print(f"Using manually specified offset: 0x{rominfo_off:x}")

    before = bytes(data[rominfo_off:rominfo_off + AND_ROMINFO_SIZE])
    data[rominfo_off:rominfo_off + AND_ROMINFO_SIZE] = b'\x00' * AND_ROMINFO_SIZE
    after = bytes(data[rominfo_off:rominfo_off + AND_ROMINFO_SIZE])

    print(f"Before: {before.hex()}")
    print(f"After:  {after.hex()}")

    out = args.output or args.image
    if out == args.image:
        bak = args.image + '.bak'
        shutil.copy2(args.image, bak)
        print(f"Backup: {bak}")

    with open(out, 'wb') as f:
        f.write(data)
    print(f"Output: {out} ({len(data):,} bytes)")
    print()
    print("Next step: re-sign with preloader-resign before flashing")
    print(f"  ./preloader-resign {out} -o {out.replace('.bin', '_signed.bin')}")


def main():
    parser = argparse.ArgumentParser(
        description="Patch MTK preloader for DA bypass (AND_ROMINFO_v zeroing)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Operations:
  --info           Show preloader structure and AND_ROMINFO_v status
  --zero-rominfo   Zero out AND_ROMINFO_v to disable DA security policy

Examples:
  # Check preloader structure
  %(prog)s preloader_a.bin --info

  # Zero AND_ROMINFO_v (DA bypass)
  %(prog)s preloader_a.bin --zero-rominfo -o preloader_a_patched.bin

  # Patch in-place (creates .bak)
  %(prog)s preloader_a.bin --zero-rominfo

  # Use a custom offset (if magic search fails)
  %(prog)s preloader_a.bin --zero-rominfo --offset 0x1288 -o patched.bin

After patching, re-sign before flashing:
  ./preloader-resign preloader_a_patched.bin -o preloader_a_final.bin
""")

    parser.add_argument("image", help="Path to preloader image")
    parser.add_argument("-o", "--output",
                        help="Output path (default: overwrite in place with .bak)")
    parser.add_argument("--offset", type=lambda x: int(x, 0),
                        help="Manually specify AND_ROMINFO_v offset (hex or decimal)")

    ops = parser.add_mutually_exclusive_group(required=True)
    ops.add_argument("--info", action="store_true",
                     help="Show preloader info and AND_ROMINFO_v status")
    ops.add_argument("--zero-rominfo", action="store_true",
                     help="Zero out AND_ROMINFO_v to disable DA security checks")

    args = parser.parse_args()

    if not os.path.isfile(args.image):
        print(f"Error: file not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    with open(args.image, 'rb') as f:
        data = f.read()

    if args.info:
        cmd_info(args, data)
    elif args.zero_rominfo:
        cmd_zero_rominfo(args, data)


if __name__ == "__main__":
    main()
