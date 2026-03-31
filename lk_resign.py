#!/usr/bin/env python3
"""Re-sign an MTK LK image with the test signing key.

Only re-signs partitions whose hashes have changed. Partitions with
correct hashes are left untouched (preserving original signatures).
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from liblk import LkImage
from liblk.constants import Magic


# MTK custom OIDs (DER-encoded)
OID_DATA_HASH = bytes.fromhex("060760867693160201")   # 2.16.886.2454.2.1
OID_HDR_HASH = bytes.fromhex("060760867693160204")    # 2.16.886.2454.2.4

DEFAULT_KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys")


def recover_trailing_certs(img):
    from liblk.structures.partition import LkPartition

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
        try:
            cert = LkPartition.from_bytes(raw[offset:], offset)
            last.certs.append(cert)
            offset = cert.end_offset
            cert_align = cert.header.alignment if cert.header.is_extended else 8
            if cert_align and offset % cert_align:
                offset += cert_align - (offset % cert_align)
        except Exception:
            break


def find_oid_offset(cert_der, oid_bytes):
    """Find the offset of the hash value following an OID in cert DER."""
    idx = cert_der.find(oid_bytes)
    if idx < 0:
        return None, None

    pos = idx + len(oid_bytes)
    tag = cert_der[pos]
    pos += 1

    # Parse length
    length = cert_der[pos]
    pos += 1
    if length & 0x80:
        num_bytes = length & 0x7F
        length = int.from_bytes(cert_der[pos:pos+num_bytes], 'big')
        pos += num_bytes

    if tag == 0x03:
        # BIT STRING: skip unused bits byte
        pos += 1
        length -= 1

    return pos, cert_der[pos:pos+32]


def find_tbs_region(cert_der):
    """Extract the TBS (to-be-signed) region from cert2 DER.

    The cert structure is:
      SEQUENCE {
        SEQUENCE { ... TBS ... }
        SEQUENCE { algorithm }
        BIT STRING { signature }
      }
    We need the bytes of the first inner SEQUENCE (the TBS).
    """
    if cert_der[0] != 0x30:
        raise ValueError("cert2 does not start with SEQUENCE")

    # Skip outer SEQUENCE tag+length
    pos = 1
    outer_len = cert_der[pos]
    if outer_len & 0x80:
        num = outer_len & 0x7F
        pos += 1 + num
    else:
        pos += 1

    # Now at the TBS SEQUENCE
    tbs_start = pos
    if cert_der[pos] != 0x30:
        raise ValueError("Expected TBS SEQUENCE")
    pos += 1
    tbs_content_len = cert_der[pos]
    if tbs_content_len & 0x80:
        num = tbs_content_len & 0x7F
        tbs_total = int.from_bytes(cert_der[pos+1:pos+1+num], 'big')
        tbs_end = pos + 1 + num + tbs_total
    else:
        tbs_end = pos + 1 + tbs_content_len

    return tbs_start, tbs_end, cert_der[tbs_start:tbs_end]


def find_signature_offset(cert_der):
    """Find the offset and length of the signature BIT STRING value in cert2."""
    if cert_der[0] != 0x30:
        raise ValueError("Not a SEQUENCE")

    # Skip outer SEQUENCE
    pos = 1
    outer_len = cert_der[pos]
    if outer_len & 0x80:
        num = outer_len & 0x7F
        pos += 1 + num
    else:
        pos += 1

    # Skip TBS SEQUENCE
    if cert_der[pos] != 0x30:
        raise ValueError("Expected TBS SEQUENCE")
    pos += 1
    length = cert_der[pos]
    if length & 0x80:
        num = length & 0x7F
        total = int.from_bytes(cert_der[pos+1:pos+1+num], 'big')
        pos += 1 + num + total
    else:
        pos += 1 + length

    # Skip AlgorithmIdentifier SEQUENCE
    if cert_der[pos] != 0x30:
        raise ValueError("Expected AlgId SEQUENCE")
    pos += 1
    length = cert_der[pos]
    if length & 0x80:
        num = length & 0x7F
        total = int.from_bytes(cert_der[pos+1:pos+1+num], 'big')
        pos += 1 + num + total
    else:
        pos += 1 + length

    # Now at signature BIT STRING
    if cert_der[pos] != 0x03:
        raise ValueError("Expected BIT STRING for signature")
    pos += 1
    length = cert_der[pos]
    pos += 1
    if length & 0x80:
        num = length & 0x7F
        sig_len = int.from_bytes(cert_der[pos:pos+num], 'big')
        pos += num
    else:
        sig_len = length

    # Skip unused bits byte
    pos += 1
    sig_len -= 1

    return pos, sig_len


def compute_hashes(part):
    """Compute header hash and alignment-padded data hash for a partition."""
    hdr_hash = hashlib.sha256(bytes(part.header)).digest()

    alignment = part.header.alignment if part.header.is_extended else 8
    data_bytes = part.data
    padded_size = len(data_bytes)
    if alignment and padded_size % alignment:
        padded_size += alignment - (padded_size % alignment)
    data_padded = data_bytes + b'\x00' * (padded_size - len(data_bytes))
    data_hash = hashlib.sha256(data_padded).digest()

    return hdr_hash, data_hash


def sign_tbs(tbs_bytes, privkey_path):
    """Sign TBS data with RSA-PSS SHA256 using openssl."""
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tbs_f:
        tbs_f.write(tbs_bytes)
        tbs_path = tbs_f.name

    sig_path = tbs_path + '.sig'
    try:
        subprocess.run([
            'openssl', 'dgst', '-sha256',
            '-sigopt', 'rsa_padding_mode:pss',
            '-sigopt', 'rsa_pss_saltlen:-1',
            '-sign', privkey_path,
            '-out', sig_path,
            tbs_path,
        ], check=True, capture_output=True)

        with open(sig_path, 'rb') as f:
            return f.read()
    finally:
        os.unlink(tbs_path)
        if os.path.exists(sig_path):
            os.unlink(sig_path)


def verify_signature(tbs_bytes, sig_bytes, pubkey_path):
    """Verify an RSA-PSS SHA256 signature using openssl."""
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tbs_f:
        tbs_f.write(tbs_bytes)
        tbs_path = tbs_f.name

    sig_path = tbs_path + '.sig'
    try:
        with open(sig_path, 'wb') as f:
            f.write(sig_bytes)

        result = subprocess.run([
            'openssl', 'dgst', '-sha256',
            '-sigopt', 'rsa_padding_mode:pss',
            '-sigopt', 'rsa_pss_saltlen:-1',
            '-verify', pubkey_path,
            '-signature', sig_path,
            tbs_path,
        ], capture_output=True)
        return result.returncode == 0
    finally:
        os.unlink(tbs_path)
        if os.path.exists(sig_path):
            os.unlink(sig_path)


def resign_image(image_path, output_path=None, keys_dir=DEFAULT_KEYS_DIR,
                 pad_size=None, force=False):
    privkey = os.path.join(keys_dir, 'img_prvk.pem')
    pubkey = os.path.join(keys_dir, 'img_pubk.pem')

    if not os.path.exists(privkey):
        print(f"Error: signing key not found: {privkey}", file=sys.stderr)
        sys.exit(1)

    # Detect original file size for padding
    original_size = Path(image_path).stat().st_size

    img = LkImage(image_path)
    recover_trailing_certs(img)

    print(f"Image: {image_path}")
    print(f"Partitions: {len(img.partitions)}")
    print()

    modified = False

    for name, part in img.partitions.items():
        if len(part.certs) < 2:
            print(f"  {name}: no cert2, skipping")
            continue

        cert2 = part.certs[1]
        cert2_der = bytearray(cert2.data)

        hdr_hash, data_hash = compute_hashes(part)

        # Check existing hashes
        data_off, emb_data_hash = find_oid_offset(cert2_der, OID_DATA_HASH)
        hdr_off, emb_hdr_hash = find_oid_offset(cert2_der, OID_HDR_HASH)

        data_ok = emb_data_hash == data_hash
        hdr_ok = emb_hdr_hash == hdr_hash

        if data_ok and hdr_ok and not force:
            print(f"  {name}: hashes OK, preserving original signature")
            continue

        # Patch hashes
        if not data_ok:
            print(f"  {name}: data hash STALE, updating")
            cert2_der[data_off:data_off+32] = data_hash
        if not hdr_ok:
            print(f"  {name}: header hash STALE, updating")
            cert2_der[hdr_off:hdr_off+32] = hdr_hash

        # Extract TBS and sign
        tbs_start, tbs_end, tbs_bytes = find_tbs_region(cert2_der)
        sig = sign_tbs(tbs_bytes, privkey)

        # Patch signature
        sig_off, sig_len = find_signature_offset(cert2_der)
        if len(sig) != sig_len:
            print(f"  {name}: WARNING signature size mismatch ({len(sig)} vs {sig_len})")
        cert2_der[sig_off:sig_off+sig_len] = sig

        # Verify
        if os.path.exists(pubkey):
            ok = verify_signature(tbs_bytes, sig, pubkey)
            print(f"  {name}: re-signed, verified={'OK' if ok else 'FAIL'}")
        else:
            print(f"  {name}: re-signed (no pubkey to verify)")

        # Write back to cert partition
        cert2._data = bytes(cert2_der)
        modified = True

    if not modified and not force:
        print("\nAll hashes already correct, no changes needed.")
        if output_path and output_path != image_path:
            shutil.copy2(image_path, output_path)
            print(f"Copied unchanged to: {output_path}")
        return

    # Rebuild image
    img._rebuild_contents()
    result = bytes(img.contents)

    # Pad to original size or specified size
    target = pad_size or original_size
    if len(result) < target:
        result = result + b'\x00' * (target - len(result))

    out = output_path or image_path
    if out == image_path:
        # Backup
        bak = image_path + '.bak'
        shutil.copy2(image_path, bak)
        print(f"\nBackup: {bak}")

    with open(out, 'wb') as f:
        f.write(result)
    print(f"Output: {out} ({len(result):,} bytes)")


def main():
    parser = argparse.ArgumentParser(
        description="Re-sign MTK LK image cert2 with test signing key")
    parser.add_argument("image", help="Path to LK image")
    parser.add_argument("-o", "--output", help="Output path (default: overwrite in place with .bak)")
    parser.add_argument("-k", "--keys", default=DEFAULT_KEYS_DIR,
                        help=f"Path to signing keys directory (default: {DEFAULT_KEYS_DIR})")
    parser.add_argument("--pad", type=int, help="Pad output to this size in bytes")
    parser.add_argument("--force", action="store_true",
                        help="Re-sign all partitions even if hashes are correct")
    args = parser.parse_args()

    resign_image(args.image, args.output, args.keys, args.pad, args.force)


if __name__ == "__main__":
    main()
