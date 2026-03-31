# MTK Preloader Analysis (MT6897 / Dimensity 8300)

Analysis of the preloader from the 557 device, a MediaTek MT6897 (Dimensity 8300) platform. The preloader is the first software stage after the Boot ROM (BROM) and is responsible for hardware initialization, DRAM training, security setup, and loading the next boot stage (LK).

## Binary Overview

- **File**: `preloader_a.bin` (4 MB UFS_BOOT container, 775 KB actual payload)
- **Architecture**: AArch64 (ARM64)
- **Load address**: `0x02000F00` (SRAM, before DRAM is available)
- **Entry point**: Register zeroing, BSS clear, data relocation, stack canary setup (`0xDEADBEFF`)
- **GFH file_len**: 793,196 bytes
- **Signature**: 1,644-byte SLA dual-block RSA-2048 PSS structure

## Binary Layout

| Offset | Size | Content |
|--------|------|---------|
| `0x00000` | 4 KB | UFS_BOOT container header + BRLYT |
| `0x01000` | 256 B | GFH header chain (6 headers) |
| `0x01100` | ~775 KB | AArch64 code + rodata + data |
| `0xC2400` | 1,644 B | SLA signature region (Block 1 + Block 2) |
| `0xC2A6C` | ~3.2 MB | Zero/0xFF padding to 4 MB |

## Boot Flow

The preloader executes 10 major stages from power-on to LK handoff:

### Stage 1: Entry and Early Init

The entry point at `0x02000F00` performs:
- Zero all general-purpose registers
- Clear BSS section
- Relocate data section
- Set up stack with canary value `0xDEADBEFF`
- Branch to main init (`platform_pre_init`)

At this point, only SRAM is available. DRAM has not been initialized yet.

### Stage 2: Watchdog and Bus Init

- Watchdog timer (WDT) initialization to prevent hang during boot
- SPMI (System Power Management Interface) bus init for PMIC communication
- Bus timeout configuration

### Stage 3: PMIC Initialization

The MT6897 uses a multi-PMIC topology. The preloader initializes all of them:

| PMIC | Role | Key Functions |
|------|------|---------------|
| **MT6363** | Main PMIC | Core voltage rails, ADC, eFuse, power sequencing |
| **MT6368** | Secondary | Additional voltage rails, sub-PMIC functions |
| **MT6375** | Charger/Type-C | USB Type-C detection, charging, auxiliary ADC |
| **MT6319** | CPU buck | CPU core voltage supply (high-performance buck) |
| **MT6685** | Clock/RTC | 32kHz clock generation, real-time clock |

19+ power rails are configured with overcurrent (OC) and power-good (PG) monitoring. Strings reference specific regulators: `VBUCK1` through `VBUCK8`, `VLDO`, `VS1`-`VS3`, `VCORE`, `VPROC`, `VGPU`, `VMODEM`, `VMD`, etc.

### Stage 4: PLL and Clock Init

All major PLLs are configured:
- `ARMPLL_LL` - Little core cluster
- `ARMPLL_BL` - Big-little core cluster
- `ARMPLL_B` - Big core cluster
- `CCIPLL` - Cache Coherent Interconnect
- `PTPPLL` - PTP (Power/Thermal/Performance)
- `CLRPLL` - Color/display
- `PHYPLL` - PHY (USB/UFS)
- `ULPOSC1` - Ultra-low power oscillator

Clock multiplexers and dividers are configured to set up the initial clock tree before DRAM init.

### Stage 5: DRAM Initialization

This is by far the most complex stage in the preloader. The DRAM subsystem includes:

**DRAM Type**: LPDDR5X, up to 8533 MHz data rate, 4 channels

**Training Sequence** (473+ strings, 20+ source files):
1. Impedance calibration (`ImpedanceCal`)
2. CBT (Command Bus Training) - `CmdBusTrainingLP45`
3. Write leveling - `WriteLeveling`
4. Gating/read DQS - `Gating`, `RxdqsGatingCal`
5. RX DQ/DQS calibration - `RxWindowPerbitCal`
6. TX DQ/DQS calibration - `TxWindowPerbitCal`
7. Duty cycle calibration - `DutyScan`
8. Jitter meter calibration - `JitterMeter`
9. Per-bit deskew calibration
10. Runtime calibration (`dramc_runtime_config`)

**Source files** (embedded paths):
```
platform/mediatek/mt6897/dramc/dramc_pi_main.c
platform/mediatek/mt6897/dramc/dramc_pi_basic_api.c
platform/mediatek/mt6897/dramc/dramc_pi_calibration_api.c
platform/mediatek/mt6897/dramc/dramc_tracking.c
platform/mediatek/mt6897/dramc/dramc_dvfs.c
platform/mediatek/mt6897/dramc/dramc_lowpower.c
platform/mediatek/mt6897/dramc/emi.c
```

**Key capabilities**:
- DDR Reserve mode for warm reboot (preserves DRAM contents across reset)
- Frequency scaling (DVFS) with multiple operating points
- Per-channel, per-rank, per-byte calibration
- Temperature-compensated refresh rate adjustment
- Memory test after training
- Calibration data can be saved to storage for faster subsequent boots

### Stage 6: Storage Initialization

**UFS (Universal Flash Storage)**:
- UFS controller init and link startup
- Device discovery and capability query
- HS-G4 (High Speed Gear 4) configuration
- Boot LUN configuration
- RPMB partition setup

**GPT (GUID Partition Table)**:
- Primary and secondary GPT reading and validation
- Partition table cached for subsequent lookups

### Stage 7: Security Initialization

This is the second most complex stage. The preloader establishes the security foundation:

**eFuse Reading**:
Security-critical fuses are read to determine the device's security posture:
- `SBC_EN` - Secure Boot Controller enable
- `SLA_EN` - Secure Boot Authentication enable
- `DAA_EN` - Download Agent Authentication enable
- `JTAG_DIS` - JTAG disable
- `SBC_PUBK_HASH` - Public key hash lock

**Image Authentication**:
The preloader verifies the next boot stage (LK) before loading it:
- Certificate chain parsing and validation
- **PKCS#1 PSS with SHA-384** for signature verification (notably SHA-384, not SHA-256)
- SoC ID binding verification (certificate must match device SOCID)
- Rollback index checking (anti-rollback protection)

Failure codes: `FAIL_IMG_AUTH`, `FAIL_CERT_AUTH`, `FAIL_HASH_AUTH`

**DICE (Device Identifier Composition Engine)**:
- Measured boot key derivation
- Device identity composition based on boot chain measurements
- Keys derived for subsequent boot stages

**RPMB Key Management**:
- iSE (integrated Secure Element) RPMB key status checking
- RPMB key programming if needed
- HMAC-based RPMB authentication

**Lock State**:
- Lock state read from `seccfg` partition
- `SEC_POLICY` framework for per-image authentication requirements
- Unlock/lock state affects which images require verification

### Stage 8: TrustZone Setup

- TEE (Trusted Execution Environment) memory reservation
- BL31/ATF (ARM Trusted Firmware) buffer allocation
- Secure and non-secure world memory partitioning
- DEVAPC (Device Access Permission Control) configuration: 39 domain entries for bus access control
- SMPU (System Memory Protection Unit) setup for north/south memory regions

### Stage 9: Download Mode

If download mode is requested (via key combo, flag, or USB connection), the preloader enters a download handshake:

**Entry conditions**:
- META mode request from host tool
- Factory mode key combination
- Download flag set in misc partition
- USB/UART connection detected during boot

**Download Agent (DA) handling**:
- DA image received over USB or UART
- DA authentication (if DAA is enabled via eFuse)
- DA loaded into DRAM and executed
- DA then handles flash operations (read/write/format)

**Supported interfaces**:
- USB (primary)
- UART (fallback)

### Stage 10: Load and Jump to LK

The final stage:
1. Load LK image from the `lk_a` or `lk_b` partition (A/B selection)
2. Authenticate the LK image (if SBC is enabled)
3. Prepare boot tags (structured data passed to LK):
   - Boot mode and boot reason
   - DRAM configuration results
   - Platform info
   - Security state
4. Jump to LK entry point

## Security Architecture

### Dual-Key Signing

The preloader's signature block contains two RSA-2048 keys:
- **Root key**: Signs the key binding certificate (Block 1). Establishes trust in the image key.
- **Image key**: Signs the content hash (Block 2). Proves the preloader binary is authentic.

Both keys match the MTK default test keys in `keys/root_pubk.pem` and `keys/img_pubk.pem`.

### eFuse Security Matrix

| Fuse | Effect |
|------|--------|
| `SBC_EN` | When set, all boot images must pass authentication |
| `SLA_EN` | Secure Boot Authentication for download mode |
| `DAA_EN` | Download Agent must be authenticated before execution |
| `JTAG_DIS` | Hardware JTAG debug interface disabled |
| `SBC_PUBK_HASH` | Locks the root public key hash - prevents key replacement |

### Authentication Algorithm

The preloader uses **PKCS#1 PSS with SHA-384** for its internal image authentication (the certificate chain used to verify LK). This is different from:
- The preloader's own signing (RSA-PSS SHA-256, salt_length=32)
- LK's cert2 signing (RSA-PSS SHA-256, salt_length=auto)

## Hardware Details

### Memory Map (Pre-DRAM)

The preloader initially runs entirely from SRAM at `0x02000F00`. DRAM is not available until after Stage 5 (DRAM training). Once DRAM is available, larger buffers are allocated there for image loading and other operations.

### DRAM Configuration

- **Type**: LPDDR5X
- **Channels**: 4
- **Maximum data rate**: 8533 MHz
- **Supported sizes**: Up to 24 GB (with relocation support)
- **Training**: Full per-channel, per-rank, per-byte calibration
- **DDR Reserve**: Warm reboot support preserving DRAM contents

### DEVAPC (Bus Access Control)

39 domain configuration entries control which bus masters can access which peripherals. This is a hardware-enforced access control mechanism that prevents unauthorized access between security domains (e.g., preventing the application processor from directly accessing modem memory).

### SMPU (System Memory Protection Unit)

Memory protection for north/south memory regions, enforcing that each subsystem can only access its allocated memory regions.

## Partition Table

The preloader reads the GPT to locate partitions. Key partitions it accesses:

| Partition | Purpose |
|-----------|---------|
| `lk_a` / `lk_b` | Next boot stage (Little Kernel) |
| `tee1` / `tee2` | Trusted Execution Environment |
| `seccfg` | Security configuration (lock state) |
| `spmfw` | System Power Manager firmware |
| `sspm` | Sub-System Power Manager |
| `mcupm` | MCU Power Manager |
| `para` | Boot parameters |
| `boot_para` | Boot parameters |
| `efuse` | eFuse data |
| `misc` | Misc data (boot command) |
| `proinfo` | Product info |

## Error Handling

### Watchdog

The watchdog timer is initialized early (Stage 2) and must be periodically serviced throughout boot. If any stage hangs (e.g., DRAM training failure), the watchdog triggers a reset.

### Assert/Fatal

The preloader includes assert/fatal error handling. Error conditions are logged via UART and may trigger:
- Watchdog reset
- Entry into download mode for recovery
- Error code display (for debug builds)

### Boot Failure Recovery

If the primary boot path fails:
- A/B slot fallback: try the other slot
- Download mode: enter BROM download mode for recovery via SP Flash Tool or mtkclient
- DDR Reserve: attempt warm reboot preserving DRAM state for crash analysis

## Debug Capabilities

### UART Logging

The preloader outputs debug logs over UART. Log verbosity depends on build type:
- `[PL]` prefix for preloader messages
- DRAM training progress and results
- PMIC initialization status
- Security verification results
- Boot mode detection

### Key Log Messages

```
[PL] platform_pre_init
[PL] pll_init
[PL] mt_mem_init (DRAM training start)
[PL] Image authentication...
[PL] Jump to LK
```

### Download Mode Debug

In download mode, the preloader communicates with host tools (SP Flash Tool, mtkclient) over USB/UART, allowing:
- Memory read/write
- Flash read/write/erase
- eFuse reading
- Device identification

## Comparison: Preloader vs LK

| Aspect | Preloader | LK |
|--------|-----------|-----|
| **Runs from** | SRAM (before DRAM) | DRAM (after DRAM init) |
| **Primary job** | Hardware init, DRAM training | Boot mode selection, kernel loading |
| **Signing** | SLA two-block (RSA-PSS SHA256, salt=32) | cert1/cert2 DER (RSA-PSS SHA256, salt=auto) |
| **Image auth** | PKCS#1 PSS SHA-384 | PKCS#1 PSS SHA-384 + AVB |
| **User interaction** | None (headless) | Display, fastboot, boot menu |
| **Download mode** | BROM/DA-based | Fastboot USB |
| **Size** | ~775 KB | ~860 KB (lk partition only) |
| **Loads next** | LK | Linux kernel |

## Source Code Structure

Embedded source paths reveal the preloader's codebase organization:

```
platform/mediatek/mt6897/
  dramc/                         - DRAM controller (20+ source files)
    dramc_pi_main.c
    dramc_pi_basic_api.c
    dramc_pi_calibration_api.c
    dramc_tracking.c
    dramc_dvfs.c
    dramc_lowpower.c
    emi.c
  pmic/                          - PMIC drivers
  pll/                           - PLL/clock init
  gpio/                          - GPIO configuration
  ufs/                           - UFS storage driver
  devapc/                        - Bus access control
  smpu/                          - Memory protection

platform/mediatek/common/
  boot/                          - Boot flow
  security/                      - Image authentication
  storage/                       - Storage abstraction
  download/                      - Download mode handler
```
