#!/usr/bin/env python3
"""Repack an unpacked MTK LK image directory back into a single image and re-sign.

Takes a directory created by lk_unpack.py, reconstructs the binary image,
and re-signs any partitions whose data/header changed.
"""

import argparse
import os
import sys
from pathlib import Path

from liblk import LkImage
from liblk.constants import Magic
from liblk.structures.partition import LkPartition


DEFAULT_KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys")


def repack(unpack_dir, original_image, output_path, keys_dir=DEFAULT_KEYS_DIR,
           pad_size=None):
    """Repack from unpacked directory, using original image as template for certs."""
    from lk_resign import recover_trailing_certs, resign_image

    # Parse the original for cert structure
    orig = LkImage(original_image)
    recover_trailing_certs(orig)

    unpack_dir = Path(unpack_dir)
    part_dirs = sorted([d for d in unpack_dir.iterdir() if d.is_dir()],
                       key=lambda d: list(orig.partitions.keys()).index(d.name)
                       if d.name in orig.partitions else 999)

    print(f"Repacking from: {unpack_dir}")
    print(f"Template image: {original_image}")
    print(f"Partitions found: {[d.name for d in part_dirs]}")
    print()

    # Build new image by replacing data in original partitions
    for pdir in part_dirs:
        name = pdir.name
        if name not in orig.partitions:
            print(f"  WARNING: {name} not in original, skipping")
            continue

        part = orig.partitions[name]

        # Load modified header if present
        hdr_path = pdir / "header.bin"
        if hdr_path.exists():
            with open(hdr_path, 'rb') as f:
                hdr_bytes = f.read()
            if hdr_bytes != bytes(part.header):
                # Update header in-place
                from ctypes import memmove, addressof, sizeof
                memmove(addressof(part.header), hdr_bytes, min(len(hdr_bytes), 512))
                print(f"  {name}: header updated")

        # Load modified data if present
        data_path = pdir / "data.bin"
        if data_path.exists():
            with open(data_path, 'rb') as f:
                new_data = f.read()
            if new_data != part.data:
                part.data = new_data
                print(f"  {name}: data updated ({len(new_data):,} bytes)")
            else:
                print(f"  {name}: data unchanged")

        # Load modified certs if present (optional override)
        for i, cert in enumerate(part.certs):
            cert_der_path = pdir / f"cert{i+1}.der"
            if cert_der_path.exists():
                with open(cert_der_path, 'rb') as f:
                    new_cert = f.read()
                if new_cert != cert.data:
                    cert._data = new_cert
                    print(f"  {name}: cert{i+1} updated")

    # Rebuild contents
    orig._rebuild_contents()

    # Determine pad size
    if pad_size is None:
        orig_size = Path(original_image).stat().st_size
        if orig_size > len(orig.contents):
            pad_size = orig_size

    # Save intermediate (before signing)
    result = bytes(orig.contents)
    if pad_size and len(result) < pad_size:
        result = result + b'\x00' * (pad_size - len(result))

    with open(output_path, 'wb') as f:
        f.write(result)

    print(f"\nRepacked to: {output_path}")

    # Now re-sign
    print("\nRe-signing...")
    from lk_resign import resign_image
    resign_image(output_path, output_path, keys_dir, pad_size)


def main():
    parser = argparse.ArgumentParser(
        description="Repack unpacked MTK LK partitions into a signed image")
    parser.add_argument("directory", help="Path to unpacked partition directory")
    parser.add_argument("original", help="Path to original LK image (template for certs)")
    parser.add_argument("-o", "--output", required=True, help="Output image path")
    parser.add_argument("-k", "--keys", default=DEFAULT_KEYS_DIR,
                        help=f"Path to signing keys directory (default: {DEFAULT_KEYS_DIR})")
    parser.add_argument("--pad", type=int, help="Pad output to this size in bytes")
    args = parser.parse_args()

    repack(args.directory, args.original, args.output, args.keys, args.pad)


if __name__ == "__main__":
    main()
