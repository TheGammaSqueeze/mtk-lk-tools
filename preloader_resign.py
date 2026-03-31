#!/usr/bin/env python3
"""Re-sign an MTK preloader image (GFH/MMM format, sig_type=5).

Supports preloader images with the MTK SLA (Secure Boot Authentication)
two-block signature structure:
  - Bare GFH (magic MMM\\x01), or UFS_BOOT/EMMC_BOOT container with BRLYT
  - GFH header chain (FILE_INFO, BL_INFO, BROM_SEC_CFG, etc.)
  - Two-block signature region at file tail: Block1 (key cert) + Block2 (image sig)

Re-signing updates Block2 only (Block1 binds keys, independent of image content):
  - Recomputes SHA256 of all file data before the signature region
  - Re-signs the Block2 TBS (header + hash, 60 bytes) with img_prvk.pem
  - Block1 (root key binding) is never modified

Signature algorithm: RSA-PSS with SHA256, MGF1-SHA256, salt_length=32.

Signature region byte-level structure (1644 bytes for RSA-2048):
  +---------+------+------------------------------------------------------+
  | Offset  | Size | Description                                          |
  +---------+------+------------------------------------------------------+
  | 0x000   |    4 | num_blocks = 2 (LE u32)                              |
  +---------+------+------------------------------------------------------+
  | BLOCK 1 (Certificate/Key Binding) - 1324 bytes                        |
  +---------+------+------------------------------------------------------+
  | 0x004   |    4 | magic = 0xE291F358                                   |
  | 0x008   |    4 | version = 0x00010000                                 |
  | 0x00C   |    4 | block_size = 1324                                    |
  | 0x010   |    4 | flags = 0x00010100                                   |
  | 0x014   |    4 | root_algo = 3 (RSA)                                  |
  | 0x018   |    4 | root_key_len = 256                                   |
  | 0x01C   |    4 | reserved = 0                                         |
  | 0x020   |    4 | root_exponent = 65537                                |
  | 0x024   |  252 | padding (zeros)                                      |
  | 0x120   |  256 | root_pubk modulus (big-endian)                       |
  | 0x220   |    4 | img_algo = 3 (RSA)                                   |
  | 0x224   |    4 | img_key_len = 256                                    |
  | 0x228   |    4 | reserved = 0                                         |
  | 0x22C   |    4 | img_exponent = 65537                                 |
  | 0x230   |  252 | padding (zeros)                                      |
  | 0x32C   |  256 | img_pubk modulus (big-endian)                        |
  | 0x42C   |    4 | sig_marker = 1                                       |
  | 0x430   |  256 | root RSA-PSS signature                               |
  +---------+------+------------------------------------------------------+
  | BLOCK 2 (Image Signature) - 316 bytes                                 |
  +---------+------+------------------------------------------------------+
  | 0x530   |    4 | magic = 0xE291F358                                   |
  | 0x534   |    4 | version = 0x00010000                                 |
  | 0x538   |    4 | block_size = 316                                     |
  | 0x53C   |    4 | flags = 0x00020100                                   |
  | 0x540   |    4 | sig_count = 1                                        |
  | 0x544   |    4 | reserved = 0                                         |
  | 0x548   |    4 | reserved = 0                                         |
  | 0x54C   |   32 | SHA256(file[0 : file_len - sig_len])                 |
  | 0x56C   |  256 | img RSA-PSS signature                                |
  +---------+------+------------------------------------------------------+
  (Offsets above are relative to sig region start = file_len - sig_len)

  Block1 TBS = sig[0x004:0x430] (magic..marker, 1068 bytes) signed by root_prvk
  Block2 TBS = sig[0x530:0x56C] (magic..hash,     60 bytes) signed by img_prvk
"""

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile


DEFAULT_KEYS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys")

# Signature block magic (stored as little-endian u32 = 0xe291f358)
SIG_MAGIC = 0xe291f358
SIG_MAGIC_BYTES = b'\x58\xf3\x91\xe2'


class PreloaderImage:
    """Parse and re-sign an MTK preloader image."""

    def __init__(self, path):
        with open(path, 'rb') as f:
            self.data = bytearray(f.read())
        self.path = path
        self._parse()

    def _parse(self):
        """Parse the preloader image structure."""
        d = self.data

        # --- Container header ---
        self.container_magic = d[0:8]
        if self.container_magic == b'UFS_BOOT':
            self.container_type = 'UFS_BOOT'
        elif self.container_magic == b'EMMC_BOOT':
            self.container_type = 'EMMC_BOOT'
        elif d[0:4] == b'\x4d\x4d\x4d\x01' or d[0:4] == b'\x4d\x4d\x4d\x00':
            # Bare GFH (no container)
            self.container_type = 'GFH_BARE'
            self.gfh_offset = 0
            self._parse_gfh()
            return
        else:
            raise ValueError(f"Unknown container magic: {self.container_magic.hex()}")

        # --- BRLYT ---
        brlyt_off = d.find(b'BRLYT')
        if brlyt_off < 0:
            raise ValueError("BRLYT header not found")
        self.brlyt_offset = brlyt_off
        self.brlyt_boot_region_addr = struct.unpack_from('<I', d, brlyt_off + 0x0c)[0]

        # --- GFH ---
        self.gfh_offset = self.brlyt_boot_region_addr
        self._parse_gfh()

    def _parse_gfh(self):
        """Parse GFH header chain and signature region."""
        d = self.data
        gfh = self.gfh_offset

        # Verify GFH magic
        magic = struct.unpack_from('<I', d, gfh)[0]
        if magic not in (0x004D4D4D, 0x014D4D4D):
            raise ValueError(f"No GFH magic at offset 0x{gfh:x}: 0x{magic:08x}")

        # Parse GFH_FILE_INFO
        self.gfh_file_info_offset = gfh
        self.fi_size = struct.unpack_from('<H', d, gfh + 4)[0]
        self.fi_type = struct.unpack_from('<H', d, gfh + 6)[0]
        self.fi_identifier = d[gfh + 8:gfh + 0x14]
        self.fi_file_ver = struct.unpack_from('<I', d, gfh + 0x14)[0]
        self.fi_file_type = struct.unpack_from('<H', d, gfh + 0x18)[0]
        self.fi_flash_dev = d[gfh + 0x1a]
        self.fi_sig_type = d[gfh + 0x1b]
        self.fi_load_addr = struct.unpack_from('<I', d, gfh + 0x1c)[0]
        self.fi_file_len = struct.unpack_from('<I', d, gfh + 0x20)[0]
        self.fi_max_size = struct.unpack_from('<I', d, gfh + 0x24)[0]
        self.fi_content_offset = struct.unpack_from('<I', d, gfh + 0x28)[0]
        self.fi_sig_len = struct.unpack_from('<I', d, gfh + 0x2c)[0]
        self.fi_jump_offset = struct.unpack_from('<I', d, gfh + 0x30)[0]
        self.fi_attr = struct.unpack_from('<I', d, gfh + 0x34)[0]

        # Derived offsets
        self.content_start = gfh + self.fi_content_offset
        self.file_end = gfh + self.fi_file_len
        self.sig_region_start = self.file_end - self.fi_sig_len

        # Validate
        if self.file_end > len(d):
            raise ValueError(
                f"GFH file_len extends beyond file: "
                f"0x{self.file_end:x} > 0x{len(d):x}")

        # --- Signature region ---
        self._parse_sig_region()

    def _parse_sig_region(self):
        """Parse the two-block signature region."""
        d = self.data
        sr = self.sig_region_start

        # Version prefix
        self.sig_version = struct.unpack_from('<I', d, sr)[0]

        # Block 1: Root key block
        self.blk1_offset = sr + 4
        blk1_magic = struct.unpack_from('<I', d, self.blk1_offset)[0]
        if blk1_magic != SIG_MAGIC:
            raise ValueError(
                f"Block1 magic mismatch at 0x{self.blk1_offset:x}: "
                f"0x{blk1_magic:08x} != 0x{SIG_MAGIC:08x}")

        self.blk1_version = struct.unpack_from('<I', d, self.blk1_offset + 4)[0]
        self.blk1_size = struct.unpack_from('<I', d, self.blk1_offset + 8)[0]

        # Root key metadata
        rk_off = self.blk1_offset + 12
        self.root_key_len = struct.unpack_from('<H', d, rk_off)[0]
        self.root_key_type = d[rk_off + 2]
        self.root_hash_type = d[rk_off + 4]

        # Root modulus location: header(12) + root_params(20) + pad(252) = +0x11c
        self.root_mod_offset = self.blk1_offset + 0x11c
        self.root_mod = bytes(d[self.root_mod_offset:self.root_mod_offset + self.root_key_len])

        # Image key modulus: root_mod_end + img_params(16) + pad(252) = +0x328
        self.img_mod_offset = self.blk1_offset + 0x328
        self.img_mod = bytes(d[self.img_mod_offset:self.img_mod_offset + self.root_key_len])

        # Signature 1 (root signs block1)
        self.sig1_offset = self.blk1_offset + self.blk1_size - self.root_key_len
        self.sig1 = bytes(d[self.sig1_offset:self.sig1_offset + self.root_key_len])

        # Block1 TBS: from magic to just before sig1
        self.blk1_tbs = bytes(d[self.blk1_offset:self.sig1_offset])

        # Block 2: Image signature block
        self.blk2_offset = self.blk1_offset + self.blk1_size
        blk2_magic = struct.unpack_from('<I', d, self.blk2_offset)[0]
        if blk2_magic != SIG_MAGIC:
            raise ValueError(
                f"Block2 magic mismatch at 0x{self.blk2_offset:x}: "
                f"0x{blk2_magic:08x} != 0x{SIG_MAGIC:08x}")

        self.blk2_version = struct.unpack_from('<I', d, self.blk2_offset + 4)[0]
        self.blk2_size = struct.unpack_from('<I', d, self.blk2_offset + 8)[0]

        # Block2 metadata
        b2m = self.blk2_offset + 12
        self.blk2_key_len = struct.unpack_from('<H', d, b2m)[0]
        self.blk2_sig_type = d[b2m + 2]

        # Content hash
        self.content_hash_offset = self.blk2_offset + 0x1c
        self.content_hash = bytes(d[self.content_hash_offset:self.content_hash_offset + 32])

        # Signature 2
        self.sig2_offset = self.blk2_offset + 0x3c
        self.sig2_len = self.blk2_key_len  # 256 for RSA-2048
        self.sig2 = bytes(d[self.sig2_offset:self.sig2_offset + self.sig2_len])

        # Block2 TBS: from magic to just before sig2 (60 bytes)
        self.blk2_tbs_offset = self.blk2_offset
        self.blk2_tbs_end = self.sig2_offset
        self.blk2_tbs = bytes(d[self.blk2_tbs_offset:self.blk2_tbs_end])

        # Verify block layout
        expected_end = self.blk2_offset + self.blk2_size
        actual_end = self.file_end
        if expected_end != actual_end:
            raise ValueError(
                f"Block2 end mismatch: 0x{expected_end:x} != 0x{actual_end:x}")

    def compute_content_hash(self):
        """Compute SHA256 of the signed content region (GFH + binary, before sig)."""
        return hashlib.sha256(
            bytes(self.data[self.gfh_offset:self.sig_region_start])
        ).digest()

    def info(self):
        """Print parsed structure information."""
        d = self.data
        print(f"File: {self.path}")
        print(f"Size: {len(d)} bytes (0x{len(d):x})")
        print(f"Container: {self.container_type}")
        print()

        print("GFH FILE_INFO:")
        print(f"  file_type:      0x{self.fi_file_type:04x}")
        print(f"  flash_dev:      0x{self.fi_flash_dev:02x}")
        print(f"  sig_type:       0x{self.fi_sig_type:02x}")
        print(f"  load_addr:      0x{self.fi_load_addr:08x}")
        print(f"  file_len:       0x{self.fi_file_len:x} ({self.fi_file_len})")
        print(f"  content_offset: 0x{self.fi_content_offset:x} ({self.fi_content_offset})")
        print(f"  sig_len:        0x{self.fi_sig_len:x} ({self.fi_sig_len})")
        print()

        print("Layout:")
        print(f"  GFH start:      0x{self.gfh_offset:06x}")
        print(f"  Content start:  0x{self.content_start:06x}")
        print(f"  Sig region:     0x{self.sig_region_start:06x}")
        print(f"  File end:       0x{self.file_end:06x}")
        print()

        print("Signature Region:")
        print(f"  Version:        {self.sig_version}")
        print(f"  Block 1 at:     0x{self.blk1_offset:06x} (size={self.blk1_size})")
        print(f"    Root modulus:  0x{self.root_mod_offset:06x} ({self.root_key_len} bytes)")
        print(f"    Img modulus:   0x{self.img_mod_offset:06x} ({self.root_key_len} bytes)")
        print(f"    Signature 1:  0x{self.sig1_offset:06x} ({self.root_key_len} bytes)")
        print(f"    TBS range:    0x{self.blk1_offset:06x} - 0x{self.sig1_offset:06x}")
        print(f"  Block 2 at:     0x{self.blk2_offset:06x} (size={self.blk2_size})")
        print(f"    Content hash: 0x{self.content_hash_offset:06x}")
        print(f"    Signature 2:  0x{self.sig2_offset:06x} ({self.sig2_len} bytes)")
        print(f"    TBS range:    0x{self.blk2_tbs_offset:06x} - 0x{self.blk2_tbs_end:06x}")
        print()

        # Hash check
        computed = self.compute_content_hash()
        match = computed == self.content_hash
        print(f"Content hash embedded: {self.content_hash.hex()}")
        print(f"Content hash computed: {computed.hex()}")
        print(f"Content hash valid:    {match}")

    def resign(self, keys_dir, verify=True):
        """Re-sign the image: update content hash and signature 2.

        Args:
            keys_dir: Directory containing img_prvk.pem (and optionally img_pubk.pem)
            verify: If True, verify signatures after re-signing

        Returns:
            True if re-signing succeeded
        """
        img_prvk = os.path.join(keys_dir, 'img_prvk.pem')
        img_pubk = os.path.join(keys_dir, 'img_pubk.pem')

        if not os.path.exists(img_prvk):
            raise FileNotFoundError(f"Signing key not found: {img_prvk}")

        # Step 1: Compute new content hash
        new_hash = self.compute_content_hash()
        old_hash = self.content_hash

        if new_hash == old_hash:
            print("Content hash unchanged - image not modified.")
            print("Re-signing anyway to prove round-trip capability.")

        # Step 2: Write new hash into Block2
        self.data[self.content_hash_offset:self.content_hash_offset + 32] = new_hash
        self.content_hash = new_hash

        # Step 3: Extract the new Block2 TBS (includes the updated hash)
        tbs = bytes(self.data[self.blk2_tbs_offset:self.blk2_tbs_end])

        # Step 4: Sign with RSA-PSS SHA256
        print(f"Signing Block2 TBS ({len(tbs)} bytes) with {img_prvk}")
        new_sig = _sign_pss_sha256(tbs, img_prvk)

        if len(new_sig) != self.sig2_len:
            raise ValueError(
                f"Signature size mismatch: got {len(new_sig)}, "
                f"expected {self.sig2_len}")

        # Step 5: Write new signature into Block2
        self.data[self.sig2_offset:self.sig2_offset + self.sig2_len] = new_sig
        self.sig2 = new_sig

        # Step 6: Verify
        if verify and os.path.exists(img_pubk):
            ok = _verify_pss_sha256(tbs, new_sig, img_pubk)
            print(f"Signature verification: {'OK' if ok else 'FAILED'}")
            if not ok:
                return False

        return True

    def save(self, output_path, pad_to=None):
        """Save the image to a file.

        Args:
            output_path: Output file path
            pad_to: If set, pad the output to this size with 0x00
        """
        result = bytes(self.data)
        if pad_to and len(result) < pad_to:
            result += b'\x00' * (pad_to - len(result))

        with open(output_path, 'wb') as f:
            f.write(result)
        return len(result)


def _sign_pss_sha256(tbs_bytes, privkey_path):
    """Sign data with RSA-PSS SHA256 using openssl."""
    with tempfile.NamedTemporaryFile(suffix='.bin', delete=False) as tbs_f:
        tbs_f.write(tbs_bytes)
        tbs_path = tbs_f.name

    sig_path = tbs_path + '.sig'
    try:
        result = subprocess.run([
            'openssl', 'dgst', '-sha256',
            '-sigopt', 'rsa_padding_mode:pss',
            '-sigopt', 'rsa_pss_saltlen:32',
            '-sign', privkey_path,
            '-out', sig_path,
            tbs_path,
        ], check=True, capture_output=True)

        with open(sig_path, 'rb') as f:
            return f.read()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"OpenSSL signing failed: {e.stderr.decode()}")
    finally:
        os.unlink(tbs_path)
        if os.path.exists(sig_path):
            os.unlink(sig_path)


def _verify_pss_sha256(tbs_bytes, sig_bytes, pubkey_path):
    """Verify RSA-PSS SHA256 signature using openssl."""
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
            '-sigopt', 'rsa_pss_saltlen:32',
            '-verify', pubkey_path,
            '-signature', sig_path,
            tbs_path,
        ], capture_output=True)
        return result.returncode == 0
    finally:
        os.unlink(tbs_path)
        if os.path.exists(sig_path):
            os.unlink(sig_path)


def main():
    parser = argparse.ArgumentParser(
        description="Re-sign MTK preloader image (GFH/UFS_BOOT format)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show image info and verify signatures
  %(prog)s --info preloader.img

  # Re-sign a modified preloader
  %(prog)s preloader.img -o preloader_signed.img

  # Re-sign in-place (creates .bak backup)
  %(prog)s preloader.img

  # Use custom key directory
  %(prog)s preloader.img -k /path/to/keys -o output.img
""")
    parser.add_argument("image", help="Path to preloader image")
    parser.add_argument("-o", "--output",
                        help="Output path (default: overwrite in place with .bak)")
    parser.add_argument("-k", "--keys", default=DEFAULT_KEYS_DIR,
                        help=f"Path to signing keys directory (default: {DEFAULT_KEYS_DIR})")
    parser.add_argument("--info", action="store_true",
                        help="Show image info and exit (no re-signing)")
    parser.add_argument("--verify", action="store_true",
                        help="Verify existing signatures and exit")
    parser.add_argument("--pad", type=int,
                        help="Pad output to this size in bytes")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip post-signing verification")

    args = parser.parse_args()

    # Parse image
    try:
        img = PreloaderImage(args.image)
    except Exception as e:
        print(f"Error parsing image: {e}", file=sys.stderr)
        sys.exit(1)

    # Info mode
    if args.info:
        img.info()
        sys.exit(0)

    # Verify mode
    if args.verify:
        img.info()
        print()
        root_pubk = os.path.join(args.keys, 'root_pubk.pem')
        img_pubk = os.path.join(args.keys, 'img_pubk.pem')

        print("Signature verification:")

        # Verify sig1 (root signs block1)
        if os.path.exists(root_pubk):
            ok1 = _verify_pss_sha256(img.blk1_tbs, img.sig1, root_pubk)
            print(f"  Block1 (root cert):  {'OK' if ok1 else 'FAILED'}")
        else:
            print(f"  Block1 (root cert):  skipped (no root_pubk.pem)")

        # Verify sig2 (img signs block2)
        if os.path.exists(img_pubk):
            ok2 = _verify_pss_sha256(img.blk2_tbs, img.sig2, img_pubk)
            print(f"  Block2 (image sig):  {'OK' if ok2 else 'FAILED'}")
        else:
            print(f"  Block2 (image sig):  skipped (no img_pubk.pem)")

        sys.exit(0)

    # Re-sign
    original_size = os.path.getsize(args.image)

    print(f"Image: {args.image}")
    print(f"Container: {img.container_type}")
    print(f"Size: {len(img.data)} bytes")
    print()

    success = img.resign(args.keys, verify=not args.no_verify)
    if not success:
        print("Re-signing FAILED", file=sys.stderr)
        sys.exit(1)

    # Save
    output_path = args.output or args.image
    pad_to = args.pad or original_size

    if output_path == args.image:
        bak = args.image + '.bak'
        shutil.copy2(args.image, bak)
        print(f"Backup: {bak}")

    written = img.save(output_path, pad_to=pad_to)
    print(f"Output: {output_path} ({written:,} bytes)")


if __name__ == "__main__":
    main()
