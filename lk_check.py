#!/usr/bin/env python3
"""Check certificates and signing keys used in an MTK LK image."""

import argparse
import hashlib
import os
import sys
from pathlib import Path

from liblk import LkImage
from liblk.constants import Magic


# MTK custom OIDs (DER-encoded OID bytes)
OID_DATA_HASH = bytes.fromhex("060760867693160201")   # 2.16.886.2454.2.1
OID_HDR_HASH = bytes.fromhex("060760867693160204")    # 2.16.886.2454.2.4
OID_IMG_PUBKEY = bytes.fromhex("060860867693160102")   # 2.16.886.2454.1.2


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


def extract_pubkey_der(cert_der):
    """Extract SubjectPublicKeyInfo from a cert2/cert1 DER structure."""
    # Look for RSA OID 1.2.840.113549.1.1.1 in SEQUENCE
    rsa_oid = bytes.fromhex("06092a864886f70d010101")
    idx = cert_der.find(rsa_oid)
    if idx < 0:
        return None

    # Walk back to find the enclosing SEQUENCE (SubjectPublicKeyInfo)
    # The SPKI starts with 0x30 (SEQUENCE) before the AlgorithmIdentifier
    for back in range(1, 20):
        pos = idx - back
        if pos < 0:
            break
        if cert_der[pos] == 0x30:
            # Parse the SEQUENCE length to get the full SPKI
            length_byte = cert_der[pos + 1]
            if length_byte & 0x80:
                num_len_bytes = length_byte & 0x7F
                total_len = int.from_bytes(cert_der[pos+2:pos+2+num_len_bytes], 'big')
                spki = cert_der[pos:pos+2+num_len_bytes+total_len]
            else:
                spki = cert_der[pos:pos+2+length_byte]
            # Verify it contains the RSA OID
            if rsa_oid in spki and len(spki) > 200:
                return spki
    return None


def find_oid_hash(cert_der, oid_bytes):
    """Find a SHA256 hash value after an OID in cert DER data."""
    idx = cert_der.find(oid_bytes)
    if idx < 0:
        return None

    pos = idx + len(oid_bytes)
    # Expect BIT STRING (0x03) or OCTET STRING (0x04)
    if pos >= len(cert_der):
        return None

    tag = cert_der[pos]
    pos += 1

    # Parse length
    if pos >= len(cert_der):
        return None
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

    return cert_der[pos:pos+32]


def key_fingerprint(spki_der):
    return hashlib.sha256(spki_der).hexdigest()[:16]


def load_pubkey_file(path):
    """Load a PEM public key and extract SubjectPublicKeyInfo DER."""
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key, Encoding, PublicFormat
        with open(path, 'rb') as f:
            pubkey = load_pem_public_key(f.read())
        return pubkey.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    except Exception:
        return None


def check_image(image_path, keys_dir=None):
    img = LkImage(image_path)
    recover_trailing_certs(img)

    # Load reference keys if available
    ref_keys = {}
    if keys_dir:
        keys_dir = Path(keys_dir)
        for name in ['img_pubk.pem', 'root_pubk.pem']:
            p = keys_dir / name
            if p.exists():
                spki = load_pubkey_file(str(p))
                if spki:
                    ref_keys[name] = spki

    print(f"Image: {image_path}")
    print(f"Size: {Path(image_path).stat().st_size:,} bytes")
    print(f"Version: {img.version}")
    print(f"Partitions: {len(img.partitions)}")
    print()

    all_cert1_fps = set()
    all_cert2_fps = set()

    for name, part in img.partitions.items():
        print(f"--- {name} ---")
        print(f"  Data: {len(part.data):,} bytes")
        print(f"  Header alignment: {part.header.alignment}")
        print(f"  Certs: {len(part.certs)}")

        # Check hashes
        if part.certs and len(part.certs) >= 2:
            cert2_der = part.certs[1].data

            # Compute expected hashes
            hdr_hash = hashlib.sha256(bytes(part.header)).digest()

            alignment = part.header.alignment if part.header.is_extended else 8
            data_bytes = part.data
            padded_size = len(data_bytes)
            if alignment and padded_size % alignment:
                padded_size += alignment - (padded_size % alignment)
            data_padded = data_bytes + b'\x00' * (padded_size - len(data_bytes))
            data_hash = hashlib.sha256(data_padded).digest()

            # Get embedded hashes
            emb_data_hash = find_oid_hash(cert2_der, OID_DATA_HASH)
            emb_hdr_hash = find_oid_hash(cert2_der, OID_HDR_HASH)

            if emb_data_hash:
                match = "OK" if emb_data_hash == data_hash else "MISMATCH"
                print(f"  Data hash: {match}")
            if emb_hdr_hash:
                match = "OK" if emb_hdr_hash == hdr_hash else "MISMATCH"
                print(f"  Header hash: {match}")

        # Check cert1
        if part.certs and len(part.certs) >= 1:
            cert1_der = part.certs[0].data
            spki = extract_pubkey_der(cert1_der)
            if spki:
                fp = key_fingerprint(spki)
                all_cert1_fps.add(fp)
                print(f"  cert1 root key: {fp}")
                if 'root_pubk.pem' in ref_keys:
                    match = "YES" if spki == ref_keys['root_pubk.pem'] else "NO"
                    print(f"  cert1 matches root_pubk.pem: {match}")

        # Check cert2
        if part.certs and len(part.certs) >= 2:
            cert2_der = part.certs[1].data
            spki = extract_pubkey_der(cert2_der)
            if spki:
                fp = key_fingerprint(spki)
                all_cert2_fps.add(fp)
                print(f"  cert2 image key: {fp}")
                if 'img_pubk.pem' in ref_keys:
                    match = "YES" if spki == ref_keys['img_pubk.pem'] else "NO"
                    print(f"  cert2 matches img_pubk.pem: {match}")

        print()

    # Summary
    print("=== Summary ===")
    print(f"Unique cert1 (root) keys: {len(all_cert1_fps)} -{', '.join(all_cert1_fps)}")
    print(f"Unique cert2 (image) keys: {len(all_cert2_fps)} -{', '.join(all_cert2_fps)}")
    if ref_keys:
        if 'root_pubk.pem' in ref_keys:
            fp = key_fingerprint(ref_keys['root_pubk.pem'])
            match = fp in all_cert1_fps
            print(f"root_pubk.pem ({fp}): {'MATCHES' if match else 'NO MATCH'}")
        if 'img_pubk.pem' in ref_keys:
            fp = key_fingerprint(ref_keys['img_pubk.pem'])
            match = fp in all_cert2_fps
            print(f"img_pubk.pem ({fp}): {'MATCHES' if match else 'NO MATCH'}")


def main():
    parser = argparse.ArgumentParser(description="Check certificates and signing keys in an MTK LK image")
    parser.add_argument("image", help="Path to LK image")
    default_keys = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys")
    parser.add_argument("-k", "--keys", default=default_keys,
                        help=f"Path to signing keys directory (default: {default_keys})")
    args = parser.parse_args()

    check_image(args.image, args.keys)


if __name__ == "__main__":
    main()
