# mtk-lk-tools

Tools for unpacking, repacking, and re-signing MediaTek (MTK) LK (Little Kernel) bootloader images.

These scripts handle the full signing workflow needed to modify MTK LK images and have them accepted by the bootloader's verified boot chain.

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

## Included Keys

The `keys/` directory contains MediaTek's default test signing keys, sourced from the alps SDK (`vendor/mediatek/proprietary/scripts/sign-image_v2/hsm_test_keys/`). Many MTK devices in development or with unlocked bootloaders use these keys.

| File | Description |
|------|-------------|
| `keys/root_prvk.pem` | Root private key (signs cert1, never used by these tools) |
| `keys/root_pubk.pem` | Root public key (used for cert1 verification checks) |
| `keys/img_prvk.pem` | Image private key (signs cert2, used for re-signing) |
| `keys/img_pubk.pem` | Image public key (used for signature verification) |

Use `lk-check` to verify whether your device's LK image uses these keys before attempting to re-sign.

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

### Notes on signature preservation

Some MTK devices appear to cache specific cert2 signature bytes in seccfg or RPMB. On these devices, re-signing a partition with a new (but cryptographically valid) RSA-PSS signature can cause boot failure, even though the signature is mathematically correct. For this reason, lk-resign only re-signs partitions whose data actually changed, and preserves original factory signatures wherever possible.

## License

The signing keys are MediaTek's publicly available test keys from the alps SDK. The scripts in this repository are provided as-is for research and development purposes.
