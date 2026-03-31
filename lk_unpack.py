#!/usr/bin/env python3
"""Unpack an MTK LK image into individual sub-partition files."""

import argparse
import os
import sys
from pathlib import Path

from liblk import LkImage


def recover_trailing_certs(img):
    """Recover cert1/cert2 for the last partition if the parser skipped them."""
    from liblk.constants import Magic

    parts = list(img.partitions.values())
    if not parts:
        return
    last = parts[-1]
    if last.certs:
        return

    raw = img.contents
    offset = last.end_offset
    alignment = last.header.alignment if last.header.is_extended else 8
    if alignment and offset % alignment:
        offset += alignment - (offset % alignment)

    while offset + 512 < len(raw):
        hdr_magic = int.from_bytes(raw[offset:offset+4], 'little')
        if hdr_magic != Magic.MAGIC:
            break
        from liblk.structures.partition import LkPartition
        try:
            cert = LkPartition.from_bytes(raw[offset:], offset)
            last.certs.append(cert)
            offset = cert.end_offset
            cert_align = cert.header.alignment if cert.header.is_extended else 8
            if cert_align and offset % cert_align:
                offset += cert_align - (offset % cert_align)
        except Exception:
            break


def unpack(image_path, output_dir):
    img = LkImage(image_path)
    recover_trailing_certs(img)

    os.makedirs(output_dir, exist_ok=True)

    print(f"Image: {image_path}")
    print(f"Version: {img.version}")
    print(f"Partitions: {len(img.partitions)}")
    print()

    for name, part in img.partitions.items():
        part_dir = os.path.join(output_dir, name)
        os.makedirs(part_dir, exist_ok=True)

        # Save header
        hdr_path = os.path.join(part_dir, "header.bin")
        with open(hdr_path, 'wb') as f:
            f.write(bytes(part.header))
        print(f"  {name}/header.bin  ({part.header.size} bytes)")

        # Save data
        data_path = os.path.join(part_dir, "data.bin")
        with open(data_path, 'wb') as f:
            f.write(part.data)
        print(f"  {name}/data.bin  ({len(part.data)} bytes)")

        # Save certs
        for i, cert in enumerate(part.certs):
            cert_name = f"cert{i+1}"

            cert_hdr_path = os.path.join(part_dir, f"{cert_name}_header.bin")
            with open(cert_hdr_path, 'wb') as f:
                f.write(bytes(cert.header))
            print(f"  {name}/{cert_name}_header.bin  ({cert.header.size} bytes)")

            cert_der_path = os.path.join(part_dir, f"{cert_name}.der")
            with open(cert_der_path, 'wb') as f:
                f.write(cert.data)
            print(f"  {name}/{cert_name}.der  ({len(cert.data)} bytes)")

        print()

    print(f"Unpacked to: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Unpack MTK LK image into sub-partitions")
    parser.add_argument("image", help="Path to LK image")
    parser.add_argument("-o", "--output", help="Output directory (default: <image>_unpacked)")
    args = parser.parse_args()

    output = args.output or f"{args.image}_unpacked"
    unpack(args.image, output)


if __name__ == "__main__":
    main()
