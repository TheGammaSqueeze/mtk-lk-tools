# Device Reference: 557 (Anbernic, MT6897)

Security model, eFuse configuration, signing key layout, and partition notes for the Anbernic 557 gaming console (MT6897 / Dimensity 8300).

## Hardware Overview

| Field | Value |
|-------|-------|
| Device | Anbernic 557 |
| SoC | MediaTek MT6897 (Dimensity 8300) |
| CPU | 4x Cortex-A510 + 3x Cortex-A715 + 1x Cortex-A715 |
| Storage | UFS |
| BSP origin | Xiaomi Duchamp (N11A-DUCHAMP / Redmi K70 Pro) |
| Board/project string | `k6897v1_64` |
| LK base address | `0xffff000050700000` |
| Preloader load address | `0x02000F00` |

## eFuse Configuration

| eFuse | State | Effect |
|-------|-------|--------|
| `sbc_en` | **0 (not blown)** | Secure Boot Controller disabled. LK, TEE, preloader, and all firmware images are loaded and executed without hardware-enforced signature verification. The cert chain is still verified by bl2_ext's internal verifier using the embedded MTK test keys, but failure does not halt boot. |
| `sla_en` | **0 (not blown)** | No SLA (Secure Boot Authentication) challenge-response required. The preloader and DA do not require an SLA handshake with SP Flash Tool or mtkclient. |
| `daa_en` | **1 (blown)** | Download Agent Authentication is active. The DA image must pass signature verification before execution. The DA is signed with Xiaomi's key (burned in eFuse as `fdd62730afd983f367b267037d1668c164ab51568485ba305621cc28d6268d96`). MTK test keys cannot sign the DA. |
| `jtag_dis` | Unknown | JTAG state not confirmed. |

The combination `sbc_en=0, daa_en=1` means:
- Boot images (LK, TEE, preloader) are free to modify and re-sign with MTK test keys
- The DA cannot be replaced or re-signed
- DA-level flash operations use Xiaomi's DA binary

## Signing Keys

All boot images on the 557 use the MTK default test signing keys from `keys/`:

| Image | Key used | Re-signable? |
|-------|----------|-------------|
| LK (`lk_a`, `lk_b`, `lk_t`) | `img_prvk.pem` (cert2) | Yes |
| TEE (`tee1`, `tee2`) | `img_prvk.pem` (cert2) | Yes |
| Preloader (`preloader_a`, `preloader_b`) | `img_prvk.pem` (Block 2) | Yes |
| MCUPM, GPUEB, SSPM, SPMFW | `img_prvk.pem` (cert2) | Container yes, payload no (encrypted) |
| DA (`DA_BR.bin`) | Xiaomi key | **No** |

Confirm which keys a specific image uses with:

```bash
./lk-check lk_a.img            # For LK/TEE images
./preloader-resign preloader_a.bin --verify  # For preloader
```

## BSP Origin: Why Xiaomi Keys for DA

Anbernic used Xiaomi's Duchamp (N11A-DUCHAMP) MT6897 BSP. Xiaomi's eFuse burn script was carried over, which includes `daa_en=1` with Xiaomi's DA key hash. Anbernic did not burn their own DA key. The result: the BROM validates the DA against Xiaomi's key hash in eFuse, and Xiaomi's `DA_BR.bin` is the only valid DA for this device.

The 557's `DA_BR.bin` header confirms this origin:

```
Version: MTK_DA_v6_2023-07-26 21:13:38
HW Code: 0x8A00 (MT6897)
Build path: /home/mi/ssd/N11A-DUCHAMP/vendor/mediatek/proprietary/...
```

## LK Image Structure

The LK image (`lk_a`) contains 5 sub-partitions:

| Sub-partition | Size | Purpose |
|---------------|------|---------|
| `lk` | ~882 KB | Main LK code. Fastboot, AVB, key detection, kernel loading |
| `bl2_ext` | ~650 KB | BL2 extension. Early init, DICE, A/B control, cert verification |
| `aee` | ~864 KB | Android Exception Engine. Crash dumps, MRDUMP, kedump |
| `lk_main_dtb` | ~399 KB | Main DTB for MT6897. Used for hardware configuration in LK |
| `lk_dtbo` | ~57 KB | DTB overlay, device-specific overrides |

All sub-partitions use extended 512-byte headers, 16-byte alignment.

## Preloader Structure

| Field | Value |
|-------|-------|
| Container | UFS_BOOT (8-byte magic at offset 0) |
| BRLYT offset | 0x200 |
| GFH offset (boot_region_addr) | 0x1000 |
| Preloader binary size | ~775 KB |
| Total preloader file size | 4 MB (padded) |
| Signature type | 5 (two-block SLA) |
| Signature size | 1,644 bytes |
| sig_region_start | GFH_offset + file_len - sig_len |
| AND_ROMINFO_v offset | 0x1288 (from file start) |

## Bootloader Unlock

The standard `fastboot flashing unlock` command does not work on the 557. The LK calls into the TEE for the 5-second unlock confirmation screen (SMC `0xC200010F`), but the device uses ADC-multiplexed joystick buttons (`singleadc-joypad`). The TEE cannot detect ADC button presses, so the confirmation always times out and auto-cancels.

The workaround is to patch the LK binary with lkpatcher. Full details in [BOOTLOADER_UNLOCK.md](BOOTLOADER_UNLOCK.md).

## DA Flash Restrictions

With the stock preloader and stock DA, the DA enforces a 7-layer security check on each partition write. This can cause SP Flash Tool and mtkclient to report flash failures even for valid partition images.

The workaround is to zero the `AND_ROMINFO_v` block in the preloader at offset 0x1288, re-sign, and flash. This makes the DA fall back to permissive mode. Full details in [PRELOADER_PATCHING.md](PRELOADER_PATCHING.md) and [DA_ANALYSIS.md](DA_ANALYSIS.md).

## Partition Table (Key Partitions)

| Partition | Purpose | Notes |
|-----------|---------|-------|
| `preloader` | Preloader (both slots share one physical partition) | UFS_BOOT container, 4 MB |
| `lk_a` / `lk_b` | LK bootloader (A/B slots) | 16 MB each |
| `lk_t` | LK recovery/test slot | Same format as lk_a |
| `tee1` / `tee2` | Trusted Execution Environment (A/B slots) | Single `atf` sub-partition |
| `seccfg` | Security configuration (lock state) | Read by LK to determine lock state |
| `spmfw_a` | System Power Manager firmware | LK cert2 format, encrypted payload |
| `sspm_a` | Sub-System Power Manager | LK cert2 format, encrypted payload |
| `mcupm_a` | MCU Power Manager (CPU DVFS) | LK cert2 format, encrypted payload |
| `gpueb_a` | GPU Embedded Board (GPU DVFS) | LK cert2 format, encrypted payload |
| `vbmeta` / `vbmeta_system` / `vbmeta_vendor` | AVB verification metadata | Controls dm-verity |

## Known Working Modifications

The following have been confirmed working on the 557:

| Modification | Method | Notes |
|-------------|--------|-------|
| LK bootloader unlock patch | lkpatcher + lk-resign | Bypasses TEE confirmation issue |
| lk_main_dtb thermal trip modification | lk-unpack + dtb edit + lk-repack + lk-resign | Device boots with modified trip points |
| lk_main_dtb GPU OPP voltage modification | lk-unpack + dtb edit + lk-repack + lk-resign | GPU voltage curve changes take effect |
| Preloader AND_ROMINFO_v zero | hex patch + preloader-resign | Enables unrestricted DA flash |
| Preloader boot normally after re-sign | preloader-resign | No signature caching issues |

## Known Limitations

| Item | Status | Reason |
|------|--------|--------|
| DA re-signing | Not possible | daa_en=1, Xiaomi key in eFuse |
| CPU DVFS modification via firmware | Not possible | MCUPM payload is encrypted |
| GPU DVFS modification via firmware | Not possible | GPUEB payload is encrypted |
| `fastboot flashing unlock` (standard) | Does not work | TEE cannot read ADC buttons |
| SLA challenge-response bypass | Not needed | sla_en=0 |

## Related Documents

- [LK_ANALYSIS.md](LK_ANALYSIS.md) - Full LK image structure and boot flow
- [PRELOADER_ANALYSIS.md](PRELOADER_ANALYSIS.md) - Preloader structure and boot flow
- [DA_ANALYSIS.md](DA_ANALYSIS.md) - Download Agent analysis
- [BOOTLOADER_UNLOCK.md](BOOTLOADER_UNLOCK.md) - LK patching for bootloader unlock
- [PRELOADER_PATCHING.md](PRELOADER_PATCHING.md) - Preloader patching for DA bypass
- [OVERCLOCKING_ANALYSIS.md](OVERCLOCKING_ANALYSIS.md) - CPU/GPU/DRAM overclocking analysis
