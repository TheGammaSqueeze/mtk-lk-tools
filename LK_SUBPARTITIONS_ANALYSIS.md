# LK Sub-Partitions Analysis (MT6897 / Dimensity 8300)

Analysis of the non-LK sub-partitions in the 557 device (Anbernic gaming console, MT6897 / Dimensity 8300) LK image: bl2_ext, aee, lk_main_dtb, and lk_dtbo.

## Sub-Partition Overview

| Partition | Size | Purpose |
|-----------|------|---------|
| **bl2_ext** | 649,728 bytes | Second-stage extension bootloader. Master image loader, display init, DICE, A/B boot control. |
| **aee** | 864,056 bytes | Android Exception Engine. Standalone crash dump binary that replaces LK during crash recovery. |
| **lk_main_dtb** | 399,039 bytes | Main device tree blob for the MT6897 platform (SoC definition). |
| **lk_dtbo** | 57,243 bytes | Device tree blob overlay (Anbernic-specific board customizations). |

---

# bl2_ext (Second-Stage Extension Bootloader)

## Boot Chain Position

```
BootROM -> Preloader (BL2) -> bl2_ext -> TF-A (BL31) + TEE (BL32) -> LK (BL33)
```

bl2_ext runs after the preloader and before the main LK. It is the **master image loader** responsible for loading ALL subsequent boot stages.

## Core Responsibilities

1. **Image loading and authentication** for 10+ boot stage images
2. **Full display initialization** (boot logo visible before LK starts)
3. **Hardware initialization** (UFS, PMIC, thermal, GPIO, I2C, DVFS, SMMU, timers)
4. **Security infrastructure** (secure boot verification, DICE certificate generation, RPMB key management, OTP anti-rollback)
5. **Memory topology setup** (30+ named reserved memory regions)
6. **A/B boot slot selection**
7. **Kernel command line construction**
8. **Device tree loading and overlay** (main DTB + DTBO + dconfig overlays)

## Images Loaded by bl2_ext

bl2_ext loads all subsequent boot stages via dedicated `app_load_*` functions:

| Function | Image | Description |
|----------|-------|-------------|
| `app_load_sspm()` | SSPM firmware | Sub-System Power Manager |
| `app_load_mcupm()` | MCUPM firmware | MCU Power Manager |
| `app_load_picachu()` | Picachu calibration | CPU calibration data |
| `app_load_bl31()` | ARM Trusted Firmware | EL3 runtime (from tee_a partition) |
| `app_load_bl32()` | TEE | Secure OS at S-EL1 |
| `app_load_bl33()` | Main LK | The main bootloader |
| `app_load_pvmfw()` | Protected VM FW | pVM firmware for Android virtualization |
| `app_load_hypervisor()` | Hypervisor | GeniZone hypervisor |
| `app_load_ise()` | ISE | Inline Security Engine |
| `app_load_kraken()` | Kraken | Kraken firmware |

Additionally loads GeniZone separately with dedicated `[GZ_INIT]` flow.

## DICE (Device Identifier Composition Engine)

bl2_ext implements DICE certificate generation for hardware-bound identity attestation, following the TCG (Trusted Computing Group) standard.

**Two CDI types generated:**
- **CDI_Attest** - Attestation identity, proves the device's software state
- **CDI_Seal** - Sealing identity, for encrypting data bound to a specific software configuration

**Certificate generation flow (CBOR/COSE encoding):**
1. `DICE_Cert_ProtectAttrEncode` - COSE protected attributes header
2. `DICE_PublicKey_CoseEncode` - Public key in COSE Key format
3. `DICE_Payload_CwtEncode` - Payload as CWT (CBOR Web Token) with device measurements
4. `DICE_Config_DescriptEncode` - Configuration descriptor (software component info)
5. `DICE_Tbs_CoseEncode` - To-Be-Signed as COSE Sign1 structure

Output is a COSE Sign1 structure stored in `vm-dice-reserved` memory region. DICE runs for both common boot stages (`security_dice_common`) and Protected VM Firmware (`security_dice_pvmfw`).

## Display Initialization

bl2_ext handles full early display bring-up:

**Panel drivers:**
- `ch13721b_V2_554_fhd_dsi_vdo` (primary)
- `ch13721b_fhd_dsi_vdo` (fallback)
- Both are FHD DSI video mode panels using the Chipone CH13721B controller IC

**Display pipeline (MT6897-specific):**
- OVL (overlay) layers: ovl0_2l through ovl3_2l
- Color processing: ccorr, c3d, gamma, dither
- Post-processing: postmask, postalign, oddmr, aal, tdshp
- Output: DSC compression, DSI0, DP interface
- DMA: RDMA, WDMA, UFBC_WDMA
- Crossbar routing for flexible pipeline configuration

**Boot logo:**
- Loaded from `logo_a`/`logo_b` partition
- Compressed and decompressed into reserved memory
- Displayed before LK starts

## A/B Boot Control

bl2_ext manages dual-slot boot selection:
- Reads `boot_control` from the `misc` partition
- Validates `BOOTCTRL_MAGIC`
- Compares slot priorities
- Returns active slot suffix via `get_suffix`
- Initializes default boot control when none exists

## Security

**Three-step image authentication:**
1. Certificate chain verification (`[SW] Cert auth.`)
2. Header authentication (`[SW] Header auth.`)
3. Image hash verification (`[SW] Hash verify`)

**SOCID binding:**
- Verifies certificate SOCID matches device SOCID
- `Socid signed in image not match with device socid!`

**Key management:**
- OEM key handling (`sec_set_oemkey`, `oemkeystore`)
- Public key hash comparison against eFuse (`pubk_hash_fuse`)
- Anti-rollback via OTP version checking

**Crypto (libtomcrypt):**
- SHA-256, SHA-384, HMAC, HKDF
- RSA-PSS (PKCS#1 PSS with SHA-384)
- AES-256-CBC decryption
- Full MTK OID table for certificate parsing

## dconfig (Dynamic Configuration)

Runtime configuration overlay system for per-device customization:
1. Reads `dconfig` partition containing environment variables + device tree overlay
2. Variables accessible via `dconfig_getenv`/`dconfig_printenv`
3. DTB overlay merged into main device tree
4. Can be SOCID-bound for device-specific configs

## Data Passed Between Boot Stages

**From Preloader (received via boottags):**
- Boot mode, boot reason, SOCID, MEID
- EMI/DRAM info, security boot state
- GeniZone parameters, debug info

**To LK/BL33 (passed forward):**
- `bl2_ext write parameter to lk`
- Kernel command line (started building)
- Merged device tree (main DTB + DTBO + dconfig)
- Display state (initialized, logo visible)
- Security state (seccfg, auth results, lock state)
- Memory block layout (30+ reserved regions)

## Source Code Structure

```
app/bl2_ext/bl2_ext.c                    - Main entry point
platform/mediatek/mt6897/sboot/sec/      - Secure boot verification
platform/mediatek/mt6897/sboot/crypto/   - Crypto library (libtomcrypt)
platform/mediatek/mt6897/disp/           - Display pipeline (8 files)
platform/mediatek/common/ufs/            - UFS storage driver
platform/mediatek/common/loader/         - Image loading
platform/mediatek/common/dtb_ops/        - DTB/DTBO handling
platform/mediatek/common/dconfig/        - Dynamic configuration
platform/mediatek/common/aee/            - AEE crash dump params
platform/mediatek/common/trustzone/      - TrustZone tags
platform/mediatek/common/dramc/          - DRAM controller
platform/mediatek/common/smmu/iommu/     - IOMMU/SMMU
lib/mblock/mblock.c                      - Memory block allocator
lib/kcmdline/kcmdline.c                  - Kernel cmdline builder
```

---

# aee (Android Exception Engine)

## What AEE Is

AEE is a **standalone replacement binary** that takes over the entire LK context when a crash is detected. It is a complete, self-contained AArch64 binary (864 KB) with its own entry point, exception vector table, full driver stack (UFS, USB, display, PMIC), and even its own fastboot implementation.

## When AEE Runs

AEE runs when DDR Reserve Mode preserves DRAM contents across a crash reset:

1. Kernel panic / WDT timeout triggers SoC reset
2. DDR Reserve Mode preserves DRAM contents
3. Device boots: preloader -> bl2_ext -> LK
4. LK detects `ddr_reserve_ready=1` and `ddr_reserve_success=1`
5. LK loads and jumps to the AEE sub-partition instead of booting normally
6. AEE reads the crashed state from preserved DRAM
7. AEE writes crash data to the `expdb` partition

**Crash sources detected:**
- `rst from: kernel` - Kernel panic/WDT timeout
- `rst from: AEE` - AEE itself crashed (re-entry)
- `rst from: BL2EXT` - BL2_EXT crash
- `rst from: lk` - LK crash
- `rst from: pl` - Preloader crash
- `rst from: DA` - Download Agent crash
- `rst from: TFA` - Trusted Firmware crash

## Binary Layout

| Region | Offset | Size | Content |
|--------|--------|------|---------|
| Entry code | 0x000-0x1000 | 4 KB | MRS CurrentEL, init branches |
| Exception vectors | 0x1000-0x3000 | 8 KB | EL1h Sync/IRQ/FIQ/SError handlers |
| Main code | 0x3000-0x78000 | 468 KB | All functions (5,916 ADRP instructions) |
| Rodata/strings | 0x78000-0x90000 | 96 KB | String literals and constants |
| Data tables | 0x90000-0xCE000 | 248 KB | EXPDB section table, crypto tables |

Virtual base: `0xffff00007e000000`

## MRDUMP (Full RAM Dump)

Full DRAM capture mechanism for post-mortem debugging.

**Magic**: `MRDUMP11`

**Features:**
- zlib compression
- AES-128-CTR encryption (configurable via `aee_encrypt_enable`)
- ELF coredump format wrapped in zip

**Output destinations:**
1. **MRDUMP partition** (primary): dedicated UFS partition
2. **SD card**: fallback storage
3. **USB (fastboot)**: dump over USB with configurable timeout (`oem usbdump_timeout_set`)

**Fastboot OEM commands:**
- `oem mrdump` - trigger dump
- `oem norts_mrdump` - dump without RTS
- `oem set_enckey` - set encryption key
- `oem usbdump_timeout_set` - set USB dump timeout

## KEDUMP (Kernel Exception Dump)

Kernel crash data capture. Source: `app/aee/KEDump.c`

**Data captured:**
- Kernel ELF core headers from preserved DRAM
- Page table traversal (L1/L2/L3) for virtual-to-physical translation
- Panic header (WDT status, exception type)
- mboot_params (CRC-validated)
- pstore/ramoops persistent store
- Raw DRAM memory regions
- Timestamps

**Two modes:**
- **Mini dump**: Critical crash state sections only
- **Full dump**: Entire DRAM via MRDUMP

## EXPDB (Exception Database)

Persistent crash data storage on the `expdb` partition. Contains 50+ registered dump sections:

### Key Dump Sections

| Section | Max Size | Content |
|---------|----------|---------|
| DFD.dfd | 32 MB | DFD MCU trace data |
| DFD_SOC.dfd | 64 MB | DFD SoC trace data |
| SYS_EMI_MBW_DUMP_BUF.gz | 16 MB | EMI memory bandwidth dump |
| SYS_LK_BL33_MEMDUMP | 12 MB | LK/BL33 memory dump |
| SYS_FULLK_LOG | 10 MB | Full kernel log |
| SYS_GPU_DFD | 4 MB | GPU DFD data |
| SYS_BL2_EXT_MEMDUMP | 4 MB | BL2_EXT memory dump |
| SYS_SCP_DUMP.gz | 2.4 MB | SCP dump (compressed) |
| SYS_VCP_DUMP | 2.5 MB | VCP dump |
| SYS_GZ_LOG_RAW | 2 MB | GeniZone hypervisor log |
| SYS_GPU_LOG_ALL | 2 MB | GPU logs |
| SYS_ADSP_LOG_ALL | 2 MB | ADSP logs |
| SYS_DRAMC_CALIBRATION_DATA | 1 MB | DRAM calibration |
| SYS_MALI_CSFFW_LOG | 1 MB | Mali GPU CSF firmware log |
| SYS_SSPM_XFILE | 1 MB | SSPM xfile |
| SYS_MCUPM_XFILE | 1 MB | MCUPM xfile |
| SYS_GPUEB_XFILE | 1 MB | GPUEB xfile |
| SYS_GPUEB_COREDUMP | 256 KB | GPU EB coredump |
| SYS_SSPM_COREDUMP | 256 KB | SSPM coredump |
| SYS_MCUPM_COREDUMP | 272 KB | MCUPM coredump |
| SYS_CPU_SYS_PI_LOG | 512 KB | CPU performance log |
| SYS_SLBC_DATA | 512 KB | System Level Bus Cache |

## Coprocessor Crash Dumps

| Coprocessor | Output Sections | Data Captured |
|-------------|----------------|---------------|
| **SCP** (System Control) | SYS_SCP_DUMP.gz (2.4 MB) | Memory dump, compressed with zip |
| **VCP** (Video) | SYS_VCP_DUMP (2.5 MB) | L2 TCM, L1 cache, registers, trace buffer, DRAM |
| **SSPM** (Power Mgr) | COREDUMP (256K) + DATA (2K) + LAST_LOG (1K) + XFILE (1M) | Core state, SPM SRAM, bus tracker |
| **GPUEB** (GPU) | COREDUMP (256K) + LAST_LOG (4K) + XFILE (1M) + EXT_DUMP (20K) | GPU registers, MFG latch/history logs |
| **ADSP** (Audio) | SYS_ADSP_LOG_ALL (2 MB) | Shared memory logs |
| **MCUPM** (MCU PM) | COREDUMP (272K) + XFILE (1M) | TCM data, core state |

## DFD (Design For Debug)

Hardware-level trace capture for crash analysis:

| Subsystem | Output | Size | Purpose |
|-----------|--------|------|---------|
| DFD MCU | DFD.dfd | 32 MB | CPU core trace capture |
| DFD SoC | DFD_SOC.dfd | 64 MB | System-level debug |
| GPU DFD | SYS_GPU_DFD | 4 MB | GPU subsystem debug |
| LastPC | (inline) | - | Last instruction address per CPU core |
| LastBus | SYS_LAST_CPU_BUS | 117 KB | Last bus transactions (timeout/error detection) |
| Return Stack | (inline) | - | CPU return address stack for call chain reconstruction |
| ETB | (inline) | - | Embedded Trace Buffer (instruction flow) |

**LastBus detects:**
- AW/AR decode errors (write/read address decode failure)
- AW/AR slave errors
- Read timeout (R stall)
- Write timeout (AW stall)

## Source Code Structure

```
app/aee/KEDump.c                         - Kernel Exception Dump
app/aee/mrdump_mpart.c                   - MRDUMP partition output
app/aee/mrdump_log.c                     - MRDUMP logging
app/fastboot/fastboot.c                  - Fastboot (for USB dump)
platform/mediatek/mt6897/disp/           - Display pipeline (for crash screen)
platform/mediatek/mt6897/sboot/          - Secure boot + crypto
platform/mediatek/common/ufs/            - UFS storage driver
platform/mediatek/common/aee/            - AEE parameters
platform/mediatek/common/sda/            - Debug info/error flags
```

---

# lk_main_dtb (Main Device Tree Blob)

## Overview

- **Magic**: 0xD00DFEED (valid FDT)
- **Version**: 17
- **Size**: 399,039 bytes
- **Model**: MT6897
- **Compatible**: `mediatek,MT6897`

The main DTB defines the entire MT6897 SoC platform: all IP blocks, bus controllers, power domains, clock trees, and PMIC register maps.

## CPU Configuration

8 cores in a 4+3+1 big.LITTLE configuration:

| Cluster | Cores | CPU Type | dmips-mhz |
|---------|-------|----------|-----------|
| cluster0 | cpu0-3 | ARM Cortex-A510 | 338 |
| cluster1 | cpu4-6 | ARM Cortex-A715 | 1080 |
| cluster2 | cpu7 | ARM Cortex-A715 (prime) | 1024 |

Interrupt controller: ARM GICv3 at 0xC400000.

## Key Hardware Blocks

### I2C (14 buses)

| Bus | Address | Key Devices |
|-----|---------|-------------|
| i2c2 | 0x11B70000 | Camera sensors (IMX766/709/866/598) |
| i2c4 | 0x11B71000 | Camera sensor (S5K3M5SX) |
| i2c5 | 0x11F01000 | MT6375 PMIC/charger, RT5133 camera LDO, RT6160 |
| i2c6 | 0x11280000 | Parade PS5170 DP mux, RT5512 speaker amp, LM3643 flash |
| i2c8 | 0x11CC0000 | Main camera (OV48B/OV64B/IMX766/IMX989/IMX758) |
| i2c9 | 0x11CC1000 | Front camera (S5K3P9SP) |
| i2c13 | 0x11CC3000 | FocalTech FT3518 touch @400kHz |

### PMICs (6 total via SPMI bus)

| PMIC | Role |
|------|------|
| **MT6363** | Main PMIC: core voltage rails, ADC, keys, power sequencing |
| **MT6368** | Secondary: additional rails, audio codec, headset detection, vibrator |
| **MT6319 x3** | Companion bucks for CPU clusters |
| **MT6685** | RTC, 32K clock, connectivity |
| **MT6375** (I2C) | Charger IC, USB-C TCPC, battery gauge |

### Storage

- **UFS**: HCI at 0x112B0000, SuperSpeed, QoS, RTFF MTCMOS
- **SD card**: MMC1 at 0x11240000, UHS SDR104, bus width 4, card detect on GPIO 33
- **SDIO**: MMC2 at 0x11242000 (disabled, for WiFi)

### USB

- USB 3.2 SuperSpeed Plus capable
- OTG mode with role-switch
- USB-C via MT6375 TCPC with PD support
- DisplayPort alt-mode via Parade PS5170 mux

### Display

- DSI0 at 0x1400D000 (active), DSI1 at 0x1420D000 (disabled)
- Full DDP pipeline with dual display pipes
- GPU: Mali Valhall at 0x13000000

---

# lk_dtbo (Device Tree Blob Overlay)

## Overview

- **Magic**: 0xD7B7AB1E (valid DTBO)
- **Entries**: 1 overlay with 54 fragments
- **Size**: 57,243 bytes

The DTBO is the **Anbernic-specific board file** that customizes the SoC platform DTB for this gaming handheld.

## Key Customizations

### Display Panel

Two panel definitions overlaid onto DSI0:
- **panel1@0**: `truly,ch13721b_V2_554,vdo` (primary)
- **panel2@1**: `truly,ch13721b,vdo` (fallback)
- PM enable GPIO 64, reset GPIO 41
- VDD: mt6368_vfp regulator

### Input / Gamepad

Three input systems defined:

**1. singleadc-joypad (ACTIVE):**
- Driver: `singleadc-joypad`, name: `retrogame_joypad`
- 6 analog axes via ADC multiplexer (left stick, right stick, triggers)
- 18 digital GPIO buttons
- Stick tuning: 100/100, trigger tuning: 200/200
- Deadzone: 1500, fuzz: 64, flat: 64

**2. SPI joystick MCU:**
- `mcu,spi_joystick` on SPI2 at 1.4 MHz
- MCU-based analog stick controller

**3. gpio-keys (DISABLED):**
- 18 buttons defined but status="disabled"
- Fallback configuration

**Critical for fastboot unlock**: Volume up/down keys are GPIO-based digital buttons through the `singleadc-joypad` driver. The PMIC "home" key is mapped to VOLUMEUP, but the TEE confirmation screen expects the PMIC key mechanism, which is why ADC joypad volume buttons cannot interact with the unlock screen.

### Cameras (4 sensors)

| Camera | I2C | Sensor | CSI Port |
|--------|-----|--------|----------|
| cam0 (main) | i2c8 | OV48B/OV64B/IMX766/IMX989/IMX758 | port-2 |
| cam1 (front) | i2c9 | S5K3P9SP | port-0 |
| cam2 | i2c2 | IMX766/IMX709/IMX866/IMX598 | port-3 |
| cam4 | i2c4 | S5K3M5SX | port-1 |

### Audio

- Speaker: Richtek RT5512 on I2C6
- Headset: MT6368 ACCDET with 4-key support
- Sound card: `mediatek,mt6897-mt6368-sound`

### Touch

- FocalTech FT3518 on I2C13 @0x38
- 10-point multitouch, 1080x1920
- Reset GPIO 60, IRQ GPIO 9

### Other

- PWM fan control
- Secondary charger RT9465 on I2C3
- Dual SIM with hot-plug (GPIO 58/59)
- GPS: L1 + L5 dual-band
- Vibrator via mt6368_vibr (3.3V)

## Main DTB vs DTBO Separation

**Main DTB** provides the MT6897 SoC platform definition:
- All IP block controllers, bus definitions, clock trees
- Complete PMIC register maps (6 PMICs)
- Power domains, interrupt routing
- Empty placeholder nodes for board-specific hardware

**DTBO** provides Anbernic board-specific customization:
- Panel model and pin assignments
- Gamepad configuration (joypad, buttons, axes)
- Camera sensor bindings
- Touch panel, speaker amp, charger
- USB-C configuration
- GPIO init table (~232 pins)
- Regulator always-on policies
