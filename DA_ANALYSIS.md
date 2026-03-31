# MTK Download Agent (DA) Analysis (MT6897 / Dimensity 8200)

Analysis of the Download Agent from the 557 device (Xiaomi Duchamp / Redmi K70 Pro, codename N11A-DUCHAMP), a MediaTek MT6897 (Dimensity 8200) platform.

The Download Agent is loaded by the BROM or preloader over USB during flash operations (SP Flash Tool, mtkclient). Once loaded into DRAM, it takes over the device and handles all flash read/write/erase operations.

## Binary Overview

- **File**: `DA_BR.bin` (1,194,924 bytes, 1167 KB)
- **Magic**: `MTK_DOWNLOAD_AGENT` at offset 0x00
- **Version**: `MTK_DA_v6_2023-07-26 21:13:38`
- **HW Code**: 0x8A00 (MT6897)
- **DA Identifier**: 0x1203DADA
- **Format**: MTK DA_BR v6 (not GFH format)
- **Build origin**: `/home/mi/ssd/N11A-DUCHAMP/vendor/mediatek/proprietary/bootable/bootloader/preloader/`

## Binary Layout

The DA is a two-stage binary. Each stage has its own code region with an appended RSA-2048 signature:

```
0x000000 - 0x00005F  DA header (magic, version, entry table)
0x000060 - 0x0000BB  Region descriptor table (4 entries, 3 unique)
0x0000BC - 0x0BD2D7  Region 1: 1st stage DA code (774,684 bytes)
0x0BD2D8 - 0x0BD31B  Region 1: RSA-2048 signature (256 bytes)
0x0BD31C - 0x123AAB  Region 3: 2nd stage DA code (419,728 bytes)
0x123AAC - 0x123BAB  Region 3: RSA-2048 signature (256 bytes)
```

## Two-Stage Architecture

### Stage 1 (Region 1) - Hardware Init

Loaded by BROM/preloader into SRAM at **0x02000000**. This is essentially a stripped-down preloader compiled from the same source tree. It handles:

- DRAM initialization (LPDDR5 calibration for MT6897)
- USB Super Speed (USB 3.x) initialization
- Hardware platform init (PLL, PMIC, clocks)
- Storage controller setup (UFS)
- Once DRAM is ready, loads and validates the 2nd stage

### Stage 2 (Region 3) - Flash Operations

Loaded into DRAM at **0x40000000**. Contains all the flash logic:

- Partition table (GPT) management
- Flash read/write/erase operations
- Security policy enforcement
- Image verification and signature checking
- USB communication protocol with host tool
- Sparse image handling

## DA Authentication

### How the BROM/Preloader Validates the DA

1. BROM loads Region 1 (1st stage) into SRAM
2. BROM verifies Region 1's 256-byte RSA-2048 signature before executing it
3. Region 1 loads Region 3 (2nd stage) into DRAM
4. Region 1 verifies Region 3's signature: `DA hash validation` / `DA hash validation OK`

### Signature Algorithm

The DA uses **RSA-2048 with PKCS#1 PSS padding and SHA-384** for signature verification. The binary contains implementations of `pkcs_1_pss_decode_sha384` and `pkcs_1_mgf1_sha384`.

### eFuse Controls for DA Authentication

| eFuse | Effect on DA |
|-------|-------------|
| `sbc_en` | Secure Boot Control - enforces DA signature verification |
| `daa_en` | Download Agent Authentication - requires DAA challenge-response |
| `sla_en` | Secure Loading Agent - requires SLA handshake with host tool |
| `jtag_dis` | JTAG disable |
| `Disable_Rom_Cmd` | Disables BROM commands |

The 557's DA reports **`DA.SLA = DISABLED`**, meaning SLA challenge-response authentication with the host tool is not required.

### Can We Re-sign the DA?

**No.** The DA's signing keys are **not** the standard MTK test keys. The embedded key hash `fdd62730afd983f367b267037d1668c164ab51568485ba305621cc28d6268d96` does not match `root_pubk.pem` or `img_pubk.pem`. This is Xiaomi's custom DA signing key (or a device-specific segment code key). Re-signing the DA would require Xiaomi's key, which we do not have.

The BROM validates the DA signature against a key hash burned in eFuse. Even if we could create a modified DA, the BROM would reject it because the signature wouldn't match the eFuse-burned key hash.

## Flash Validation - Why the DA Rejects Images

This is the core issue: the DA implements a **7-layer security check** for every partition write operation. Understanding this explains why even valid dump images get rejected.

### Layer 1: SEC_POLICY Per-Partition Check

Source: `sec_policy_wrapper2.c`

For every partition write, the DA looks up a security policy:

```
[SEC_POLICY] sboot_state = 0x%x    (from eFuse sbc_en)
[SEC_POLICY] lock_state = 0x%x      (from seccfg partition)
policy of partition: %s
  hash_binding = %d
  img_auth_required = %d
  dl_forbidden = %d
```

Three flags per partition:
- **dl_forbidden**: If 1, write is completely blocked. Error: `Security deny for [%s].`
- **img_auth_required**: If 1, the image's internal signature must be verified before writing
- **hash_binding**: If 1, the image hash must match a stored binding value

**Default policy**: When a partition name is not in the explicit policy table, the DA uses a default policy: `[SEC_POLICY] reached the end, use default policy`. When `sbc_en=1`, the default policy likely sets `img_auth_required=1`.

### Layer 2: External Signature Check

The host tool can send external `.sig` files alongside partition images:

```
CMD:SECURITY-SET-ALLINONE-SIGNATURE
Security deny [%s] Signature(*.sig) invalid.
```

SP Flash Tool sends these .sig files, but they must be generated with the correct signing key. Without the OEM's signing key, the .sig files are invalid.

### Layer 3: Entity (Internal Image) Signature Check

The DA verifies the image's internal certificate chain (for images that use the LK partition format with cert1/cert2):

```
Security deny [%s] Signature(Entity) invalid[0x%x].
```

This checks:
- cert1 (root certificate) signature validity
- cert2 (image certificate) signature validity
- cert2's embedded hashes match the actual image data

The DA uses all MTK custom OIDs for verification:
- `2.16.886.2454.2.1` - Image data hash
- `2.16.886.2454.2.4` - Image header hash
- Plus OIDs for cert1/cert2/cert3 extensions

**Critical detail**: The DA verifies cert1 against the **eFuse-burned public key hash** (`fuse pubk hash =`), not against a key embedded in the DA itself. If the eFuse hash matches the MTK test root key, our re-signed images would pass this check. If the eFuse hash is a custom OEM key, they would fail.

### Layer 4: Image Hash Binding Check

```
image hash binding check fail / pass
image hash list check fail / ok
expected hash = ...
```

Compares the image hash against a stored binding. This is used for anti-clone protection and to ensure only authorized image versions are flashed.

### Layer 5: Anti-Rollback Version Check

```
downloaded img ver (%d) < otp ver (%d)
downloaded img ver (%d) < storage img ver (%d)
```

Compares the image's embedded version against:
1. The OTP (eFuse-burned) minimum version - cannot be bypassed
2. The storage-stored version - can potentially be reset

If the image version is older than either stored version, the write is rejected.

### Layer 6: SECCFG Anti-Clone

```
[%s] SECCFG Anti-Clone Enabled
```

The DA reads the `seccfg` partition (magic `AND_ROMINFO_v`) and enforces device-binding. This prevents images from one device being flashed to another.

### Layer 7: Partial Protection

```
partial_protect is enabled, start...
whole region is in protection.
```

Certain byte ranges within partitions can be individually protected from writes. If the write range falls entirely within a protected region, the write is blocked.

### Why lk_a Specifically Gets Rejected

The partition name `lk_a` does **not** appear in the DA's hardcoded protected partition list. The rejection flow is:

1. DA receives `CMD:WRITE-PARTITION` for `lk_a`
2. DA looks up `sec_policy` for "lk_a" - not found in explicit table
3. Falls back to default policy (when `sbc_en=1`, this sets `img_auth_required=1`)
4. DA attempts to verify the image:
   a. No external `.sig` file provided (or invalid) - Layer 2 fails
   b. Internal cert chain check: cert1 verified against eFuse pubk hash
   c. If eFuse hash is Xiaomi's key (not MTK test key), cert1 verification fails
   d. Even if certs pass, hash binding check may fail
5. Result: `Security deny [lk_a] Signature(Entity) invalid` or similar

### Why mtkclient and fastboot Work

- **mtkclient** bypasses the DA entirely. It either runs its own custom DA or operates at the BROM level, completely sidestepping the security policy enforcement
- **fastboot** goes through LK's fastboot handler, which does its own simpler write path that doesn't go through the DA's security policy stack
- **dd from a root shell** writes directly to the block device, bypassing all signature checks

## Preloader Special Handling

Preloader writes receive additional verification beyond the standard 7 layers:

```
Preloader hash binding check fail/pass
Preloader image sig check fail/ok
check preloader version failed/passed
Preloader do not contain HB
```

The DA also manages UFS boot LU write protection for preloader writes:
- Clears write protection on boot1/boot2 LUs before writing
- Re-applies write protection after writing
- `ufs_set_write_protect` / `ufs_clr_write_protect`

## Communication Protocol

The DA uses an XML-over-USB protocol. The host tool sends commands and the DA responds with results:

### Flash Commands

| Command | Description |
|---------|-------------|
| `CMD:WRITE-PARTITION` | Write data to a named partition |
| `CMD:WRITE-FLASH` | Write data to a raw flash address |
| `CMD:READ-PARTITION` | Read data from a named partition |
| `CMD:READ-FLASH` | Read data from a raw flash address |
| `CMD:ERASE-PARTITION` | Erase a named partition |
| `CMD:ERASE-FLASH` | Erase a raw flash address range |
| `CMD:FLASH-ALL` | Flash all partitions from a firmware package |
| `CMD:FLASH-UPDATE` | OTA-style firmware update |
| `CMD:WRITE-PARTITIONS` | Write multiple partitions |

### System Commands

| Command | Description |
|---------|-------------|
| `CMD:NOTIFY-INIT-HW` | Initialize hardware |
| `CMD:GET-HW-INFO` | Get hardware information |
| `CMD:GET-DA-INFO` | Get DA version and capabilities |
| `CMD:SET-HOST-INFO` | Set host tool information |
| `CMD:SET-RUNTIME-PARAMETER` | Configure runtime parameters |
| `CMD:HOST-SUPPORTED-COMMANDS` | Query supported commands |
| `CMD:BOOT-TO` | Boot to a specific mode |
| `CMD:REBOOT` | Reboot device |
| `CMD:SET-BOOT-MODE` | Set boot mode |

### Security Commands

| Command | Description |
|---------|-------------|
| `CMD:SECURITY-SET-ALLINONE-SIGNATURE` | Provide external signature files |
| `CMD:WRITE-PRIVATE-CERT` | Write private certificate |
| `CMD:WRITE-EFUSE` | Write eFuse values |
| `CMD:READ-EFUSE` | Read eFuse values |

### Debug Commands

| Command | Description |
|---------|-------------|
| `CMD:RAM-TEST` | Run DRAM test |
| `CMD:DEBUG:DRAM-REPAIR` | DRAM repair diagnostics |
| `CMD:DEBUG:UFS` | UFS debug operations |
| `CMD:DEBUG:UFS-EYE-MONITOR` | UFS signal quality monitoring |
| `CMD:READ-REGISTER` | Read hardware register |
| `CMD:WRITE-REGISTER` | Write hardware register |
| `CMD:GET-SYS-PROPERTY` | Get system properties |

### Result Codes

| Result | Meaning |
|--------|---------|
| `Rslt.OK` | Success |
| `Rslt.FlashError` | Flash write/read error |
| `Rslt.FlashChksumError` | Flash checksum mismatch |
| `Rslt.UsbChksumError` | USB transfer checksum error |
| `Rslt.StorageNotReady` | Storage not initialized |

## Protected Partition List

The following partitions have size-change protection during firmware update (the DA blocks writes if the partition size changes):

```
init_boot_a/b, vendor_boot_a/b, odm_a/b, vbmeta_a/b,
tee1, tee2, cache, secro, oemkeystore, userdata,
modem_a/b, modem_1_a/b, md1dsp, md1arm7, md3img,
sgpt, pgpt, cam_vpu1-3, spmfw/SPMFW, mcupmfw/MCUPMFW,
nvram, protect1/2, nvcfg, persist, odmdtbo_a/b,
persistent_a/b, vbmeta_system_a/b, vbmeta_vendor_a/b,
super_a/b, mcf2_a/b, mcf_ota_a/b, rootfs_a/b, cust, rescue
```

Note: `lk_a` is **not** in this list, so size-change protection is not the cause of lk_a write rejection.

## Flash Operation Details

- **Storage**: UFS (primary for MT6897), with eMMC fallback support
- **USB**: Super Speed (USB 3.x) for fast transfers
- **Sparse images**: Full sparse image parsing (sparse header, chunk types)
- **Checksumming**: SHA-256 for image verification (`CHK_SHA256`)
- **Dual-buffer pipeline**: Overlaps USB receive with storage write for throughput
- **Speed reporting**: Tracks and reports KB/s for storage and USB operations
- **Progress**: Sends `CMD:PROGRESS-REPORT` XML messages to host tool

## Hardware Crypto

The DA uses the HACC (Hardware Access Control Cipher) engine for:
- Key derivation from hardware unique key
- RPMB key generation
- Secure key wrapping
- Source: `da2/sec_sej_sk.c`

## Debug and Logging

- **Log channels**: UART, USB-STORAGE, TRACE, DEBUG, WARN, NONE
- **Configurable via**: `da/arg/da_log_level`, `da/arg/log_channel`
- **Progress reporting**: XML-based progress reports to host tool
- **DRAM diagnostics**: Full DRAM test and repair via `CMD:RAM-TEST` and `CMD:DEBUG:DRAM-REPAIR`

## Summary: Workarounds for DA Image Rejection

| Method | How it works | Limitations |
|--------|-------------|-------------|
| **fastboot** | Bypasses DA entirely, uses LK's fastboot handler | Requires LK to be running, some partitions may still be restricted |
| **mtkclient** | Runs custom DA or operates at BROM level, bypasses all security | Requires USB connection in BROM mode |
| **dd from root shell** | Direct block device write, no signature checks | Requires root access and booted system |
| **SP Flash Tool with correct .sig** | Provides valid external signatures to satisfy DA checks | Requires OEM signing key (not available) |

The DA's security model is designed so that even dumped-from-device images may be rejected when written back, because the security checks verify against eFuse-burned values and seccfg state that may not match the image's certificates. This is by design to prevent unauthorized firmware modification on production devices.
