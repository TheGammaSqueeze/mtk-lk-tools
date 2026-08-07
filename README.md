# mtk-lk-tools

Tools for unpacking, repacking, re-signing, and patching MediaTek (MTK) LK (Little Kernel) bootloader and preloader images.

These scripts handle the full signing workflow needed to modify MTK LK and preloader images and have them accepted by the bootloader's verified boot chain.

## Device Guides and Analysis

| Document | Contents |
|----------|---------|
| [DEVICE_557.md](DEVICE_557.md) | 557 (Anbernic, MT6897) eFuse config, signing key layout, known-working mods |
| [BOOTLOADER_UNLOCK.md](BOOTLOADER_UNLOCK.md) | LK binary patching for bootloader unlock, lkpatcher workflow |
| [PRELOADER_PATCHING.md](PRELOADER_PATCHING.md) | Preloader AND_ROMINFO_v patch for DA bypass, re-signing workflow |
| [LK_ANALYSIS.md](LK_ANALYSIS.md) | LK image structure, sub-partitions, boot flow, fastboot, AVB |
| [LK_SUBPARTITIONS_ANALYSIS.md](LK_SUBPARTITIONS_ANALYSIS.md) | Per-sub-partition analysis |
| [PRELOADER_ANALYSIS.md](PRELOADER_ANALYSIS.md) | Preloader structure, GFH headers, signing structure |
| [DA_ANALYSIS.md](DA_ANALYSIS.md) | Download Agent analysis, 7-layer security, AND_ROMINFO |
| [TEE_ANALYSIS.md](TEE_ANALYSIS.md) | Trusted Execution Environment image analysis |
| [OVERCLOCKING_ANALYSIS.md](OVERCLOCKING_ANALYSIS.md) | CPU/GPU/DRAM overclocking, what can be modified |
| [FIRMWARE_DECRYPTION_ANALYSIS.md](FIRMWARE_DECRYPTION_ANALYSIS.md) | Encrypted firmware (MCUPM, GPUEB) analysis |

## Requirements

- Python 3.8+
- [liblk](https://github.com/nickelc/liblk) - `pip install liblk`
- [cryptography](https://pypi.org/project/cryptography/) - `pip install cryptography`
- OpenSSL CLI (for RSA-PSS signing/verification)

## Usage

### Check what keys an LK image uses

```bash
./lk-check lk_a.img
./lk-check lk_a.img -k /path/to/custom/keys
```

Shows each sub-partition's certificate details, verifies embedded hashes against computed values, and checks whether the image was signed with the included MTK test keys.

### Unpack an LK image

```bash
./lk-unpack lk_a.img
./lk-unpack lk_a.img -o my_output_dir
```

Extracts each sub-partition into its own directory with separate files for the header, data, cert1, and cert2. Output defaults to `<image>_unpacked/`.

The unpacked structure looks like:

```
lk_a.img_unpacked/
  lk/
    header.bin        # 512-byte partition header
    data.bin          # Partition payload (code/data)
    cert1_header.bin  # cert1 MTK header
    cert1.der         # cert1 DER certificate
    cert2_header.bin  # cert2 MTK header
    cert2.der         # cert2 DER certificate
  bl2_ext/
    ...
  aee/
    ...
  lk_main_dtb/
    ...
```

### Re-sign an LK image

```bash
./lk-resign lk_a.img                          # In-place (creates .bak)
./lk-resign lk_a.img -o lk_a_signed.img       # Output to new file
./lk-resign lk_a.img --force                   # Re-sign all, even unchanged
./lk-resign lk_a.img -k /path/to/custom/keys  # Use custom keys
```

Smart re-signing: computes fresh hashes for each sub-partition and compares them against the hashes already embedded in cert2. Only partitions with stale hashes get re-signed. Partitions that haven't changed keep their original signatures, which is important because some devices may reject valid-but-different RSA-PSS signatures if the original signature bytes are cached in seccfg/RPMB.

### Repack an unpacked image

```bash
./lk-repack unpacked_dir/ original.img -o output.img
```

Takes a directory produced by `lk-unpack`, reconstructs the binary image using the original image as a template for certificate structure, then automatically re-signs any partitions whose data changed.

## Typical workflow

```bash
# 1. Unpack
./lk-unpack lk_a.img -o work/

# 2. Modify the partition you want (e.g. patch the lk binary)
# ... edit work/lk/data.bin ...

# 3. Repack and re-sign
./lk-repack work/ lk_a.img -o lk_a_patched.img

# 4. Flash
fastboot flash lk lk_a_patched.img
```

Or if you already have a modified LK image and just need to fix the signatures:

```bash
./lk-resign modified_lk.img -o signed_lk.img
```

### Re-sign a TEE image

TEE (Trusted Execution Environment) images use the same LK partition format (cert1/cert2 DER chain) and the same signing keys. The `lk-resign` and `lk-check` tools work directly on TEE images:

```bash
./lk-check tee_a                          # Check TEE signing keys and hashes
./lk-resign tee_a -o tee_a_signed         # Re-sign after modification
./lk-unpack tee_a -o tee_unpacked/        # Unpack the atf partition
```

A TEE image typically contains a single `atf` (ARM Trusted Firmware) partition with cert1 + cert2.

### Re-sign a preloader image

```bash
./preloader-resign preloader.bin --info              # Show structure and verify hashes
./preloader-resign preloader.bin --verify             # Verify both signatures
./preloader-resign preloader.bin -o preloader_out.bin  # Re-sign to new file
./preloader-resign preloader.bin                       # In-place (creates .bak)
```

Supports all MTK preloader container formats: bare GFH, UFS_BOOT, and EMMC_BOOT. Parses the GFH header chain, locates the SLA signature region, recomputes the content hash, and re-signs Block 2 with the image private key. Block 1 (root key certificate) is never modified.

### Preloader workflow

```bash
# 1. Check current signing keys
./preloader-resign preloader.bin --verify

# 2. Modify the preloader binary (hex editor, binary patch, etc.)
# ... modify preloader.bin ...

# 3. Re-sign
./preloader-resign preloader.bin -o preloader_signed.bin

# 4. Flash
fastboot flash preloader preloader_signed.bin
```

### Patch LK for bootloader unlock

On MTK devices where `fastboot flashing unlock` fails or where the TEE confirmation screen cannot accept input (e.g., the 557's ADC joystick buttons), patch the LK binary directly to make it report an always-unlocked state.

This requires [lkpatcher](https://github.com/R0rt1z2/lkpatcher) (`pip install lkpatcher`).

```bash
# 1. Unpack the LK image
./lk-unpack lk_a.img -o lk_unpacked/

# 2. Patch the LK binary (fastboot, dm_verity, orange_state, red_state patches)
python3 -m lkpatcher lk_unpacked/lk/data.bin -o lk_unpacked/lk/data.bin

# 3. Repack and re-sign (write to /tmp to avoid NTFS issues)
./lk-repack lk_unpacked/ lk_a.img -o /tmp/lk_patched.img

# 4. Confirm re-signing
./lk-resign /tmp/lk_patched.img

# 5. Flash
fastboot flash lk_a /tmp/lk_patched.img
fastboot flash lk_b /tmp/lk_patched.img
```

After flashing and rebooting to fastboot, `fastboot getvar unlocked` should return `yes`. The four patch categories applied by lkpatcher:

- **fastboot**: forces the unlock-state check to always return 0 (unlocked)
- **dm_verity**: suppresses vbmeta/dm-verity state warnings
- **orange_state**: suppresses the LCS/orange-state warning screen on boot
- **red_state**: suppresses device verification failure output

See [BOOTLOADER_UNLOCK.md](BOOTLOADER_UNLOCK.md) for a full explanation of how the patches work, the needle byte sequences, and the 557-specific TEE/ADC button issue.

### Bypass DA flash restrictions via preloader patch

On devices where the Download Agent (DA) rejects partition writes (even for valid images), you can patch the preloader to zero out the AND_ROMINFO_v block. The DA reads this block to determine security policy; zeroing it causes the DA to fall back to permissive mode, allowing unrestricted writes via SP Flash Tool or mtkclient.

```bash
# Check preloader structure and AND_ROMINFO_v status
./preloader-patch preloader_a.bin --info

# Zero AND_ROMINFO_v
./preloader-patch preloader_a.bin --zero-rominfo -o preloader_a_patched.bin

# Re-sign the patched preloader
./preloader-resign preloader_a_patched.bin -o preloader_a_final.bin

# Flash to both preloader slots
fastboot flash preloader_a preloader_a_final.bin
fastboot flash preloader_b preloader_a_final.bin
```

For UFS_BOOT preloaders (MT6897, most modern MTK), AND_ROMINFO_v is at offset 0x1288 from the file start. The tool searches for the magic automatically and falls back to this known offset if not found.

See [PRELOADER_PATCHING.md](PRELOADER_PATCHING.md) for the full explanation and [DA_ANALYSIS.md](DA_ANALYSIS.md) for why this works.

### Important: Windows/NTFS mount workaround

When writing output to a Windows mount (`/mnt/c/` in WSL), always write to a Linux filesystem first, then copy:

```bash
# DO THIS (write to /tmp, then copy)
./lk-repack work/ original.img -o /tmp/output.img
./lk-resign /tmp/output.img
cp /tmp/output.img /mnt/c/path/to/output.img

# DON'T DO THIS (resign may silently fail on Windows mounts)
./lk-repack work/ original.img -o /mnt/c/path/to/output.img
```

The `shutil.copy2` backup step can fail on NTFS metadata operations, which in older versions would crash before writing the re-signed output. The tools now handle this gracefully, but writing to a Linux filesystem first is safest.

## Included Keys

The `keys/` directory contains MediaTek's default test signing keys, sourced from the alps SDK (`vendor/mediatek/proprietary/scripts/sign-image_v2/hsm_test_keys/`). Many MTK devices in development or with unlocked bootloaders use these keys.

| File | Description |
|------|-------------|
| `keys/root_prvk.pem` | Root private key (signs cert1, never used by these tools) |
| `keys/root_pubk.pem` | Root public key (used for cert1 verification checks) |
| `keys/img_prvk.pem` | Image private key (signs cert2, used for re-signing) |
| `keys/img_pubk.pem` | Image public key (used for signature verification) |

Use `lk-check` or `preloader-resign --verify` to verify whether your device's images use these keys before attempting to re-sign. LK images, TEE images, and preloaders all use the same key pair, just with different signing structures (cert1/cert2 DER for LK and TEE, two-block SLA for preloaders).

## How MTK LK Image Signing Works

### Image structure

An MTK LK image is a concatenation of sub-partitions. A typical LK image contains four: `lk`, `bl2_ext`, `aee`, and `lk_main_dtb`. Each sub-partition has this layout:

```
+------------------+
| Header (512 B)   |  MTK partition header with magic 0x58881688
+------------------+
| Data (variable)  |  Partition payload (code, device tree, etc.)
+------------------+
| Padding          |  Zero-pad to alignment boundary
+------------------+
| cert1 header     |  512-byte MTK header for cert1
+------------------+
| cert1 DER        |  Root certificate (DER-encoded)
+------------------+
| Padding          |
+------------------+
| cert2 header     |  512-byte MTK header for cert2
+------------------+
| cert2 DER        |  Image certificate (DER-encoded)
+------------------+
| Padding          |
+------------------+
```

The last sub-partition's final cert has `image_list_end=1` in its MTK header, marking the end of the image. Everything after that is typically zero-padded to fill the flash partition.

### Two-certificate chain

MTK LK uses a two-certificate chain for verified boot:

**cert1 (root certificate):** Signed by the root private key. Contains the root public key and embeds the image public key under a custom MTK OID (`2.16.886.2454.1.2`). This certificate is never modified during re-signing. It establishes that the image public key is trusted by the root key.

**cert2 (image certificate):** Signed by the image private key. Contains the image public key and embeds two SHA256 hashes under custom MTK OIDs:

- OID `2.16.886.2454.2.1` - SHA256 hash of the partition data
- OID `2.16.886.2454.2.4` - SHA256 hash of the 512-byte partition header

When the bootloader verifies an LK image, it checks that:
1. cert1's signature is valid under the root public key (burned into the device or in efuse)
2. cert1 embeds the same public key that cert2 uses
3. cert2's signature is valid under the image public key
4. The hashes embedded in cert2 match the actual partition header and data

### Data hash alignment padding

This is the most critical detail and the one most likely to cause silently broken images: **the data hash is not computed on the raw data bytes.** Instead, the data is zero-padded to the next multiple of the partition header's `alignment` field before hashing.

For example, if a partition's data is 180,165 bytes and the alignment is 16:

- 180,165 % 16 = 5, so 11 bytes of zero padding are appended
- SHA256 is computed over 180,176 bytes (the data + 11 zero bytes)

The header hash does not use padding. It is simply SHA256 of the raw 512-byte header.

If you compute the data hash without alignment padding, the resulting image will have a valid RSA signature over the wrong hash, and the bootloader will reject it. The image will fail to boot with no useful error message.

### Signing algorithm

cert2 is signed with **RSA-PSS** using:

- Hash: SHA-256
- Mask generation function: MGF1 with SHA-256
- Salt length: auto (`-1`, which uses the maximum salt length)

The equivalent OpenSSL command is:

```bash
openssl dgst -sha256 \
    -sigopt rsa_padding_mode:pss \
    -sigopt rsa_pss_saltlen:-1 \
    -sign keys/img_prvk.pem \
    -out sig.bin \
    tbs.bin
```

Because RSA-PSS uses a random salt, signatures are non-deterministic. Signing the same data twice produces different (but equally valid) signature bytes. This is expected behavior.

### cert2 ASN.1 structure

cert2 is a DER-encoded ASN.1 structure (similar to X.509 but with custom MTK OIDs instead of standard extensions). The high-level layout is:

```
SEQUENCE {                      # Outer wrapper
  SEQUENCE {                    # TBS (to-be-signed) region
    INTEGER serial
    SEQUENCE { algorithm }
    SEQUENCE { issuer }
    SEQUENCE { validity }
    SEQUENCE { subject }
    SEQUENCE { subjectPublicKeyInfo }
    # Custom MTK OID fields:
    SEQUENCE { OID 2.16.886.2454.2.1, BIT STRING <data_hash> }
    SEQUENCE { OID 2.16.886.2454.2.2, INTEGER 0 }
    SEQUENCE { OID 2.16.886.2454.2.4, BIT STRING <header_hash> }
    SEQUENCE { OID 2.16.886.2454.2.6, INTEGER 0 }
    SEQUENCE { OID 2.16.886.2454.2.7, UTF8String "0" }
    SEQUENCE { OID 2.16.886.2454.2.8, INTEGER 0 }
    SEQUENCE { OID 2.16.886.2454.2.9, INTEGER 0 }
    SEQUENCE { OID 2.16.886.2454.3.1, INTEGER 0 }
    SEQUENCE { OID 2.16.886.2454.3.2, INTEGER 1 }
  }
  SEQUENCE { signatureAlgorithm }
  BIT STRING { signature }       # 256-byte RSA-PSS signature
}
```

The re-signing process:
1. Patch the two hash values in the TBS region
2. Extract the complete TBS SEQUENCE bytes
3. Sign the TBS bytes with RSA-PSS SHA256
4. Replace the signature BIT STRING value with the new signature

### How the scripts work

**lk_check.py** parses the image, extracts the SubjectPublicKeyInfo from each cert1 and cert2, fingerprints them, and compares against the reference keys in `keys/`. It also recomputes the alignment-padded data hash and header hash for each partition and reports whether they match the values embedded in cert2.

**lk_unpack.py** uses the liblk library to parse the image into its constituent partitions, then writes each component (header, data, cert1, cert2) as separate files. It handles the edge case where the liblk parser stops at the last partition's `image_list_end=1` flag before reaching trailing certificates, by manually scanning for additional MTK headers after the last parsed partition.

**lk_resign.py** parses the image, computes fresh hashes for each partition (with alignment padding), and compares them against the hashes already embedded in cert2. If a hash has changed, it patches the new hash into cert2, re-extracts the TBS region, generates a new RSA-PSS signature using the image private key, patches the signature in, and verifies it. Partitions with unchanged hashes are left completely untouched, preserving their original signatures.

**lk_repack.py** takes an unpacked directory and an original image as inputs. It uses the original image as a structural template (preserving certificate headers and cert chain), replaces partition headers and data from the unpacked directory, rebuilds the binary image, and then calls lk_resign to fix any stale hashes. The output is padded to match the original image size.

## How MTK Preloader Signing Works

Preloader images use a completely different signing structure from LK images, even though they use the same RSA keys.

### Container formats

A preloader binary can be wrapped in one of three container formats:

- **UFS_BOOT**: 16-byte header with magic `UFS_BOOT`, followed by a BRLYT (boot region layout table) at offset 0x200, with the actual preloader at the BRLYT's `boot_region_addr` (typically 0x1000). Used by devices with UFS storage (MT6897, etc.).
- **EMMC_BOOT**: Similar container for eMMC-based devices, with magic `EMMC_BOOT`.
- **Bare GFH**: No container wrapper. The file starts directly with the GFH header chain (magic `MMM\x01`). Used by some older platforms (MT8168, etc.).

The container is just a wrapper. The signing structure is the same regardless of container format.

### GFH header chain

Inside the container (or at the start of a bare GFH file), the preloader begins with a chain of GFH (Generic File Header) structures. The first is always `GFH_FILE_INFO`, which contains:

| Field | Description |
|-------|-------------|
| `file_len` | Total length of the preloader (GFH headers + content + signature region) |
| `sig_type` | Signature type. Must be `5` for the two-block SLA structure |
| `sig_len` | Size of the signature region at the tail (1,644 bytes for RSA-2048) |
| `content_offset` | Offset from GFH start to the binary content |

The signature region occupies the last `sig_len` bytes of the preloader (from `file_len - sig_len` to `file_len`).

### Two-block SLA signature structure

The signature region (1,644 bytes for RSA-2048 keys) contains a 4-byte prefix followed by two signature blocks:

```
+----------------------------------------------------+
| Prefix: num_blocks = 2 (4 bytes, LE u32)           |
+----------------------------------------------------+
| Block 1: Key Binding Certificate (1,324 bytes)     |
|   - Root public key modulus (256 bytes)             |
|   - Image public key modulus (256 bytes)            |
|   - RSA-PSS signature by root key (256 bytes)       |
+----------------------------------------------------+
| Block 2: Image Signature (316 bytes)               |
|   - SHA256 hash of content (32 bytes)              |
|   - RSA-PSS signature by image key (256 bytes)      |
+----------------------------------------------------+
```

**Block 1** is a key binding certificate. It contains both public keys and is signed by the root private key. It proves that the image public key is authorized by the root key. This block is independent of the preloader content and never needs to be modified.

**Block 2** is the image signature. It contains a SHA256 hash of the preloader content and is signed by the image private key. This block must be re-signed whenever the preloader binary is modified.

### Detailed Block 1 layout (1,324 bytes)

```
Offset  Size  Field
------  ----  -----
0x000      4  Magic: 0xE291F358 (LE)
0x004      4  Version: 0x00010000
0x008      4  Block size: 1324
0x00C      4  Flags: 0x00010100
0x010      4  Root key algo: 3 (RSA)
0x014      4  Root key length: 256
0x018      4  Reserved: 0
0x01C      4  Root exponent: 65537
0x020    252  Padding (zeros)
0x11C    256  Root public key modulus (big-endian)
0x21C      4  Image key algo: 3
0x220      4  Image key length: 256
0x224      4  Reserved: 0
0x228      4  Image exponent: 65537
0x22C    252  Padding (zeros)
0x328    256  Image public key modulus (big-endian)
0x428      4  Signature marker: 1
0x42C    256  RSA-PSS signature (root signs bytes 0x000-0x42C)
```

TBS (to-be-signed) for Block 1: bytes 0x000 through 0x42C (1,068 bytes), signed by `root_prvk.pem`.

### Detailed Block 2 layout (316 bytes)

```
Offset  Size  Field
------  ----  -----
0x000      4  Magic: 0xE291F358 (LE)
0x004      4  Version: 0x00010000
0x008      4  Block size: 316
0x00C      4  Flags: 0x00020100
0x010      4  Hash count: 1
0x014      4  Reserved: 0
0x018      4  Reserved: 0
0x01C     32  SHA256 of content (from GFH start to sig region start)
0x03C    256  RSA-PSS signature (img signs bytes 0x000-0x03C)
```

TBS for Block 2: bytes 0x000 through 0x03C (60 bytes, including the content hash), signed by `img_prvk.pem`.

### Content hash coverage

The SHA256 hash in Block 2 covers all bytes from the GFH start (where the `MMM` magic is) up to the start of the signature region. This includes the GFH header chain and the entire preloader binary, but not the signature region itself.

For a preloader inside a UFS_BOOT container at offset 0x1000 with `file_len=0xC1A6C` and `sig_len=0x66C`:
- Hash covers: bytes `[0x1000, 0xC2400)` (that is, `file_len - sig_len` bytes starting from the GFH offset)

### Signing algorithm

Both blocks use **RSA-PSS** with:

- Hash: SHA-256
- Mask generation function: MGF1 with SHA-256
- Salt length: **32** (fixed, not auto/-1 like LK images)

This is a key difference from LK image signing, which uses `salt_length=-1` (auto/max). Using the wrong salt length will produce signatures that fail verification.

The equivalent OpenSSL command for preloader signing:

```bash
openssl dgst -sha256 \
    -sigopt rsa_padding_mode:pss \
    -sigopt rsa_pss_saltlen:32 \
    -sign keys/img_prvk.pem \
    -out sig.bin \
    tbs.bin
```

### Re-signing process

Only Block 2 needs to be updated when the preloader content changes:

1. Compute `SHA256(data[gfh_offset : sig_region_start])`
2. Write the new hash into Block 2 at offset 0x01C (32 bytes)
3. Extract the Block 2 TBS region (60 bytes: from magic through the hash)
4. Sign the TBS with RSA-PSS SHA256 (salt_length=32) using `img_prvk.pem`
5. Write the 256-byte signature into Block 2 at offset 0x03C

Block 1 is never modified because it only contains key bindings, not content hashes.

### How preloader_resign.py works

The tool parses the container format (UFS_BOOT, EMMC_BOOT, or bare GFH) to find the GFH header chain. It reads the `GFH_FILE_INFO` to determine `file_len` and `sig_len`, which gives the exact boundaries of the signature region. It then parses the two-block SLA structure using the `0xE291F358` magic markers.

For re-signing, it recomputes the SHA256 content hash, patches it into Block 2, re-signs the Block 2 TBS with the image private key, and writes the new signature. The output is padded to match the original file size.

In `--verify` mode, it independently verifies both Block 1 (against `root_pubk.pem`) and Block 2 (against `img_pubk.pem`) to confirm the image is properly signed.

### Notes on signature preservation

Some MTK devices appear to cache specific cert2 signature bytes in seccfg or RPMB. On these devices, re-signing a partition with a new (but cryptographically valid) RSA-PSS signature can cause boot failure, even though the signature is mathematically correct. For this reason, lk-resign only re-signs partitions whose data actually changed, and preserves original factory signatures wherever possible.

## License

The signing keys are MediaTek's publicly available test keys from the alps SDK. The scripts in this repository are provided as-is for research and development purposes.
