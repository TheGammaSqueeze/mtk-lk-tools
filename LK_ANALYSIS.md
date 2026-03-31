# MTK LK Bootloader Analysis (MT6897 / Dimensity 8300)

Analysis of the LK (Little Kernel) bootloader from the 557 device, a MediaTek MT6897 (Dimensity 8300) platform.

## Image Overview

- **Architecture**: AArch64 (ARM64)
- **LK base address**: `0xffff000050700000`
- **LK code size**: 881,872 bytes (861 KB)
- **Image version**: 2 (v2 format with extended headers)

## Sub-Partitions

The LK image contains 5 sub-partitions, each with cert1 + cert2 certificates:

| Partition | Size | Purpose |
|-----------|------|---------|
| **lk** | 881,872 bytes | Main LK bootloader code. Contains the entire boot flow: hardware init, display, fastboot, AVB verification, kernel loading. |
| **bl2_ext** | 649,728 bytes | BL2 extension. Runs before the main LK. Handles early init, DICE certificate generation, A/B boot control, logo loading, display init, dconfig, certificate verification, and SOCID matching. |
| **aee** | 864,056 bytes | Android Exception Engine crash dump handler. Handles kernel exceptions, mrdump, kedump, EXPDB writes, DFD data collection, coprocessor core dumps (SSPM, SCP, GPUEB, ADSP, VCP), and crash log storage. |
| **lk_main_dtb** | 399,039 bytes | Main device tree blob (DTB) for the MT6897 platform. DTB v17. Contains I2C controllers, display components, SoC node definitions. |
| **lk_dtbo** | 57,243 bytes | Device tree blob overlay (DTBO). Contains device-specific overlays applied on top of the main DTB. |

All sub-partitions use extended headers (512 bytes), 16-byte alignment.

## Boot Flow and Initialization

The boot sequence follows LK's two-phase init system:

1. **bootstrap** / **bootstrap2** - LK's standard two-phase init inherited from the Little Kernel project
2. **platform_pl_boottags hooks** - Parse boot tags passed from the preloader (boot mode, boot reason, etc.)
3. **arm_generic_timer_init** - ARM generic timer setup
4. **mblock_init** - Memory block system initialization
5. **log_store_lk_init** - Log storage initialization
6. **keypad_init** - Keypad/button detection for boot mode selection
7. **mt_boot_init** - Main MediaTek boot init, determines boot mode based on preloader tags and key state
8. **fastboot_init** - Fastboot mode initialization (if selected)
9. **boot_linux_from_storage** - Load kernel image from storage
10. **boot_linux_fdt** - Prepare FDT and boot Linux
11. **"lk finished --> jump to linux kernel"** - Final handoff to the kernel

### Supported Boot Modes

- **Normal Mode** - Standard Android boot
- **Fastboot Mode** - USB fastboot protocol
- **Recovery Mode** - Android recovery (supports recovery-as-boot with GKI)
- **Factory Mode** - Factory testing
- **META Mode** - MediaTek Engineering Test Architecture (for factory/debug)
- **Power Off Charging Mode (KPOC)** - Charge while powered off
- **Normal Boot + ftrace** - Normal boot with kernel ftrace enabled
- **Normal Boot + initcall** - Normal boot with initcall debug

A boot menu is available with volume key navigation: `[VOLUME_UP to select. VOLUME_DOWN is OK.]`

### Key Detection

The LK reads both hardware and software key states:
- `kpd_sw_pwrkey` / `kpd_hw_pwrkey` - Power key
- `kpd_sw_rstkey` / `kpd_hw_rstkey` - Reset key
- `kpd_hw_recovery_key` / `kpd_hw_factory_key` - Recovery and factory keys

Key nodes are configured via device tree: `mediatek,hw-pwrkey`, `mediatek,sw-pwrkey`, `mediatek,hw-recovery-key`, `mediatek,hw-factory-key`, `mediatek,gpio_key_index`.

Note: The 557 device uses ADC-based buttons (`singleadc-joypad`) configured via device tree, not standard GPIO keys. The TEE cannot detect these ADC buttons, which is why the fastboot unlock confirmation times out.

## Verified Boot / Secure Boot

### AVB (Android Verified Boot)

The LK includes a full copy of Google's libavb library with source files from `external/lib/libavb/`. It implements:

- VBMeta header and signature verification
- Chain partition support with rollback index checking (`Image rollback index is less than the stored rollback index.`)
- Hash descriptor and hashtree descriptor verification
- dm-verity state management

Partitions verified via AVB: `vbmeta`, `vbmeta_system`, `vbmeta_vendor`, `boot`, `init_boot`, `vendor_boot`.

### Boot States

The LK reports one of four verified boot states to Android via `androidboot.verifiedbootstate=`:

| State | Meaning |
|-------|---------|
| **green** | Fully verified, bootloader locked |
| **orange** | Bootloader unlocked |
| **yellow** | Custom root of trust (custom AVB key) |
| **red** | Verification failure |

### Image Authentication Flow

For each image loaded, the LK performs a three-stage verification:
1. **Certificate chain authentication** (`[SW] Cert auth.`)
2. **Header authentication** (`[SW] Header auth.`)
3. **Image hash verification** (`[SW] Hash verify`)

Failure codes: `LK_AUTH_CERT_CHAIN_FAIL`, `LK_AUTH_HEADER_AUTH_FAIL`, `LK_AUTH_IMG_AUTH_FAIL`.

### SOCID Binding

Images can be bound to a specific SoC ID. The LK verifies:
- `Image is not signed with socid!`
- `Socid signed in image not match with device socid!`

### Secure Boot Controller (SBC)

Hardware-level secure boot enforcement: `[SBC] sbc_en = %d`. When enabled, SBC requires all loaded images to pass authentication.

### Crypto Implementation

The MT6897 LK includes a full crypto library at `platform/mediatek/mt6897/sboot/crypto/libtomcrypt/`:
- SHA256, SHA384 hashing
- HMAC-SHA256
- HKDF key derivation
- PKCS#1 PSS (SHA384) signature verification
- AES-256-CBC decryption
- Hardware crypto engine acceleration (`crypto_hw_tfa_init_internal`)

An embedded 2048-bit RSA public key at offset 0xa5960 in the LK data is used for AVB verification.

## Fastboot Mode

### Standard Commands

| Command | Description |
|---------|-------------|
| `download:` | Download data to device |
| `flash:` | Flash a partition |
| `erase:` | Erase a partition |
| `reboot` | Reboot device |
| `reboot-bootloader` | Reboot to bootloader |
| `reboot-fastboot` | Reboot to userspace fastboot |
| `reboot-recovery` | Reboot to recovery |
| `getvar:` | Get a device variable |
| `flashing lock` | Lock the bootloader |
| `flashing unlock` | Unlock the bootloader |
| `flashing get_unlock_ability` | Check if unlock is allowed |

### OEM Commands

| Command | Description |
|---------|-------------|
| `oem cdms` | CDMS (vendor feature) |
| `oem dump_pllk_log` | Dump PLL/clock log |
| `oem get_key` | Get key information |
| `oem get_socid` | Get SoC ID |
| `oem off-mode-charge` | Control off-mode charging |
| `oem p2u` | P2U control (on/off) |
| `oem printk-ratelimit` | Control printk rate limiting |
| `oem set_enckey` | Set encryption key |
| `oem ultraflash:` / `oem ultraflash_en` | Ultraflash mode |
| `oem usb2jtag` | Enable/disable USB2JTAG debug |

### Fastboot Variables

Key variables available via `getvar`:
- `product`, `serialno`, `version-baseband`, `version-bootloader`
- `max-download-size` - Maximum download buffer size
- `secure` / `secured` - Secure boot state
- `partition-type:` / `partition-size:` - Per-partition info
- `is-userspace` - Userspace fastboot flag
- `off-mode-charge` - Off-mode charging state
- `slot-count`, `slot-retry-count:a/b`, `slot-successful:a/b`, `slot-unbootable:a/b` - A/B slot info

### Unlock Flow

The unlock/lock flow involves a 5-second user confirmation screen:

1. LK checks `is_unlock_allowed` and `unlock_ability`
2. If allowed, displays confirmation: "Unlock bootloader?" / "Lock bootloader?"
3. Volume keys select Yes/No, with a 5-second auto-cancel timeout
4. On the 557, the TEE handles the confirmation screen via SMC `0xC200010F`. Because the device uses ADC joypad buttons that the TEE cannot detect, the confirmation always times out and auto-selects "cancel"
5. Success/failure message displayed for 3 seconds before returning to fastboot

Lock state strings: "unlocking the bootloader will also delete all personal data", "a custom OS is not subject to the same testing as the original OS".

## Display and UI

### Display Pipeline

The MT6897 has a complex multi-stage display pipeline:

- **OVL** (Overlay): 4x 2-layer overlay engines (`disp_ovl0_2l` through `disp_ovl3_2l`)
- **RSZ** (Resize): 2 resize engines
- **Color/CCORR/C3D** - Color processing stages
- **AAL** (Ambient Adaptive Lighting)
- **GAMMA/POSTMASK/DITHER** - Gamma correction and dithering
- **SPR** (Sub-Pixel Rendering)
- **DSC** (Display Stream Compression)
- **DSI** - MIPI DSI output (CPHY mode: `mediatek,mt6897-mipi-tx-cphy`)
- **DP_INTF** - DisplayPort interface
- **WDMA** - Write-back DMA engines
- **Merge** - Multi-panel/high-res merging

### Panel Driver

Two FHD DSI video mode panel drivers are included:
- `ch13721b_fhd_dsi_vdo` - Primary panel
- `ch13721b_V2_554_fhd_dsi_vdo` - V2 variant

The `ch13721b` is the panel IC driver. Panel init includes ID reading and MIPI TX voltage configuration.

### Backlight

LED/backlight is configured via device tree at `/mtk-leds/backlight` and `/mtk-leds1/backlight` with `max-brightness` and `max-hw-brightness` properties. Supports both I2C and connector LED types.

### Logo

Boot logo is loaded from the `logo` partition (with A/B variants: `logo_a`, `logo_b`, `logo_t`). The logo is compressed and decompressed during boot.

## Storage and Partitions

### UFS Storage

The primary storage is UFS (Universal Flash Storage), not eMMC:
- UFS version detection and High Speed Gear mode configuration (`HS-G%d-%d, %d lane`)
- HPB (Host Performance Booster) support
- RPMB region management
- OTP partition support
- Write protection and Secure Write Protect Configuration Block
- Boot LUN configuration

### Referenced Partition Names

**Core Android:**
`boot`/`boot_a`/`boot_b`, `init_boot`, `vendor_boot`, `recovery`/`recovery_a`/`recovery_b`, `system`/`system_b`, `vendor`, `super`, `userdata`, `cache`, `metadata`, `misc`

**Bootloader/Firmware:**
`preloader`/`preloader_a`/`preloader_b`, `lk_a`/`lk_b`/`lk_crash`/`lk_t`, `bl2_ext`, `lk_main_dtb`, `lk_dtbo`, `spmfw`, `sspm`, `mcupm`/`mcupmfw`, `scp1`/`scp2`/`scpctl`/`scp_crash_dump`, `vcpctl`, `dpm`/`dpmpt`/`dpmdm`/`dpmpm`, `connsys`, `adsp`

**Security:**
`tee1`/`tee1_a`/`tee1_b`, `tee2`/`tee2_a`/`tee2_b`, `gz_t`, `seccfg`, `sec1`, `vbmeta`/`vbmeta_system`/`vbmeta_vendor`, `efuse`, `otp`, `oemkeystore`, `frp`

**Data/Config:**
`proinfo`, `para`, `boot_para`, `protect2`, `persist`, `dconfig`/`dconfig-dt`, `logo`/`logo_a`/`logo_b`/`logo_t`, `dtbo`, `md1img`, `expdb`, `mrdump`, `oem`

## Security Features

### RPMB (Replay Protected Memory Block)

Extensive RPMB support for UFS storage:
- HMAC-based authentication
- Write counter verification
- Nonce-based replay protection
- Secure Write Protect Configuration Block read/write
- iSE (integrated Secure Element) RPMB key status checking

### TEE / TrustZone / GeniZone

- TF-A (Trusted Firmware-A) TEE loading from `tee1`/`tee2` partitions
- GeniZone hypervisor support (`gz_t` partition)
- GeniZone init and mblock management
- MediaTek TEE support (`mtee_support`)
- SMC calls for various subsystems (APUSYS, DFD, etc.)
- Hypervisor memory management (`ZhyP` magic, `hyp_unmap2()`)

### eFuse / OTP

- eFuse security control for SBC, SLA, DAA, JTAG disable
- PMIC eFuse reading (MT6363)
- UFS OTP locking
- Public key hash burned in eFuse (`pubk_hash_fuse =`)
- Aging eFuse values for calibration

### DEVAPC / Memory Protection

- Device Access Permission Control for bus access control
- APU subsystem DEVAPC
- System Memory Protection Unit (SMPU)
- EMI MPU for modem isolation

### DICE (Device Identifier Composition Engine)

BL2_EXT implements DICE for measured boot:
- CBOR/COSE encoding for DICE certificates
- Public key and TBS COSE encoding
- Device identity binding via SOCID
- Support for pVMFW (protected Virtual Machine Firmware)

## Hardware Initialization

### PMIC

Multiple PMICs are managed:
- **MT6363** (main): ADC, eFuse, power sequence controller, key detection, dummy load
- **MT6685**: RTC (Real-Time Clock)
- **MT6375**: USB Type-C support, auxiliary ADC

### GPIO / I2C

- LCD reset, bias enable/disable GPIOs
- Pin control configuration via device tree
- Multiple I2C buses for peripheral communication
- I2C error handling (ACK errors, HS-NACK errors)

### USB

- USB gadget mode for fastboot
- QMU (Queue Management Unit) for DMA transfers
- USB3 SuperSpeed support
- USB2JTAG debug mode

### Thermal

- LVTS (Low Voltage Temperature Sensor) temperature reading
- GPU temperature monitoring
- Thermal reboot handling
- PMIC thermal shutdown management

## Device Tree and Kernel Handoff

### FDT Manipulation

The LK extensively modifies the device tree before passing it to the kernel. Dedicated FDT set functions handle:
- Boot info, META mode info, secure boot info
- AVB cmdline parameters
- DRAM controller and EMI info
- Display debug info
- Chip ID, model, UFS info
- Firmware info and IMIX resistance

### UFDT Overlay

Full UFDT (Unified FDT) overlay support applies device-specific overlays from the `lk_dtbo` sub-partition onto the main DTB from `lk_main_dtb`.

### Kernel Loading

- Boot image header v3 format support
- Vendor boot image support
- Kernel KASLR seed generation for ASLR
- Memory Tagging Extension (MTE) setup
- Ramdisk loading (regular and vendor ramdisk)
- Bootconfig support (`#BOOTCONFIG`)

### A/B Slot Support

Full A/B OTA support with:
- Priority-based slot selection
- Slot attributes: retry count, successful, unbootable
- Active slot setting
- `androidboot.slot_suffix=%s` cmdline parameter

### Key Cmdline Parameters Passed to Kernel

```
androidboot.bootreason=reboot
androidboot.serialno=%s
androidboot.slot_suffix=%s
androidboot.verifiedbootstate=green|orange|yellow|red
androidboot.boot_devices=bootdevice,soc/%08lx.ufshci,%08lx.ufshci
androidboot.ddr_size=%llu
androidboot.dtb_idx=0 androidboot.dtbo_idx=%d
earlycon=uart8250,mmio32,0x%x
console=ttyS%d,%dn1
```

## Coprocessors Managed by LK

The MT6897 has numerous coprocessors that the LK initializes:

| Coprocessor | Architecture | Purpose |
|-------------|-------------|---------|
| **SCP** (System Control Processor) | RISC-V 55 | General-purpose system control, sensor hub |
| **VCP** (Video Coprocessor) | RISC-V 55 | Video processing offload |
| **SSPM** (Sub-System Power Manager) | Dedicated | Power management subsystem |
| **MCUPM** (MCU Power Manager) | RISC-V 33 | MCU cluster power management |
| **GPUEB** (GPU Embedded Board) | RISC-V 33 | GPU power/clock management, DFD |
| **APUSYS** (AI Processing Unit) | RISC-V 33 | AI/ML accelerator control |
| **ADSP** (Audio DSP) | Dedicated | Audio processing |

Each coprocessor has dedicated reserved memory, firmware loading, and crash dump support.

## Crash Dump / AEE

The `aee` sub-partition (Android Exception Engine) provides comprehensive crash handling:

- **MRDUMP**: Full RAM dump to storage
- **KEDUMP**: Kernel Exception Dump
- **EXPDB**: Exception database in dedicated partition
- **DFD** (Design For Debug): MCU/SoC/GPU trace capture, LastPC, LastBus, bus tracker
- **Coprocessor dumps**: SSPM, SCP, VCP, GPUEB, ADSP core dumps
- **BL2_EXT/BL33 memory dumps**
- Support for dump to SD card and USB

## Connectivity

- WiFi/BT/GPS via MediaTek CONNAC: firmware loading for WiFi, BT, GPS from `CONN_RO` partitions
- Connectivity EMI reserved memory region
- ConnSys device tree node: `mediatek,mt6897-consys`

## Modem (CCCI)

Extensive modem support through CCCI (Cross Core Communication Interface):
- MD1 image loading from `md1img` partition
- Multiple MD header format support (v1, v3, v6)
- Shared memory allocation (normal and C-share regions)
- Bank remapping (BANK0-BANK4)
- EMI MPU region setup for modem isolation
- DRDI (Dynamic Radio Data Interchange) support
- SCP integration for modem communication

## Source Code Structure

The LK codebase is organized as:

```
app/fastboot/                    - Fastboot application
app/mt_boot/                     - MTK boot application
app/mt_boot/avb/                 - AVB verification
arch/arm64/                      - ARM64 architecture (MMU)
dev/timer/arm_generic/           - ARM generic timer
external/lib/libavb/             - Google's AVB library
external/lib/libavb_user/        - AVB user operations
lib/kcmdline/                    - Kernel cmdline builder
lib/mblock/                      - Memory block allocator
platform/mediatek/common/
  aee/                           - AEE/mrdump
  apusys_rv/                     - APUSYS RISC-V
  dramc/                         - DRAM controller
  dtb_ops/                       - DTB/DTBO operations
  emi/                           - EMI info
  loader/                        - Image loader
  sboot/                         - Secure boot
  scp/                           - SCP subsystem
  ufs/                           - UFS driver
platform/mediatek/mt6897/
  clkbuf/                        - Clock buffer
  disp/                          - Display pipeline
  sboot/crypto/libtomcrypt/      - Crypto library
  sboot/sec/                     - Secure boot implementation
```

## Debug Capabilities

- UART serial console: `earlycon=uart8250,mmio32`, `console=ttyS%d`
- MET (MediaTek Event Tracing): enabled only in orange (unlocked) state
- USB2JTAG debug mode via `oem usb2jtag` fastboot command
- Trace events: `sched_switch`, `cpu_frequency`, `sched_wakeup`, `workqueue`, `module_load`
- Debug boot modes: Normal Boot + ftrace, Normal Boot + initcall
- Initcall debug: `initcall_debug=1 log_buf_len=4M printk.devkmsg=on`
- KASAN support: `kasan.fault=panic`

## Platform Identification

- **Board/project**: `k6897v1_64`
- **Chip variants**: `MT6897Z/TZA`, `MT6897Z/ZA`
- **DRAM**: Up to 24GB supported (with relocation support)
- **Panel IC**: `ch13721b` (FHD DSI video mode)
- **RTC**: MT6685
