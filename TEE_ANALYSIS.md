# MTK TEE/ATF Analysis (MT6897 / Dimensity 8300)

Analysis of the TEE image from the 557 device, a MediaTek MT6897 (Dimensity 8300) platform. The TEE image contains the ARM Trusted Firmware (BL31) that runs at Exception Level 3 (EL3) in the secure world.

## Image Overview

- **File**: `tee_a` (5,242,880 bytes total, 409,128 bytes ATF payload)
- **Format**: MTK LK partition format with cert1 + cert2 (same as LK images)
- **Partition name**: `atf` (ARM Trusted Firmware)
- **Signing keys**: Same MTK default test keys as LK and preloader
- **Binary type**: Flat binary (not ELF, no FDT, no embedded sub-images)
- **Load address**: `0x48800000`
- **TF-A version**: lts-v2.8.13 (release), git hash `b58be1e3548`, built Sep 10 2025
- **Architecture**: AArch64, ARMv9.0

## What the TEE Image Actually Contains

Despite the name "TEE", the `atf` partition in the tee_a image is **ARM Trusted Firmware BL31** (the EL3 runtime firmware), not the TEE OS itself. The actual TEE OS (MediaTek's proprietary MTEE) runs at S-EL1 and is loaded as BL32 by BL31, but the BL32 binary is not embedded in this partition.

The boot chain is:
1. **Preloader** loads and authenticates the tee_a image
2. **BL31 (this binary)** initializes the secure world, sets up SMC handling, configures memory protection
3. **BL31 loads BL32** (MTEE TEE OS) into its reserved memory region
4. **BL31 hands off to BL33** (LK) in the normal world
5. BL31 remains resident at EL3, handling SMC calls from both normal and secure worlds

## CPU Topology

The MT6897 has a tri-cluster 4+3+1 CPU configuration:

| Cluster | Cores | Architecture | Purpose |
|---------|-------|-------------|---------|
| Little | 4x Cortex-A510 | ARMv9.0 | Efficiency |
| Big | 3x Cortex-A715 | ARMv9.0 | Performance |
| Super | 1x Cortex-X3 | ARMv9.0 | Peak performance |

BL31 manages all 8 cores for power state transitions, hotplug, and secure world entry/exit.

## SMC (Secure Monitor Call) Services

BL31 handles 92 SMC services, categorized by caller:

### SMC Services Called by LK (33 services)

| SMC ID | Name | Description |
|--------|------|-------------|
| 0xC2000101 | MTK_SIP_LK_SBC_GET_LOCK_STATE | Get SBC lock state |
| 0xC2000102 | MTK_SIP_LK_SBC_GET_DAA_ENABLE | Get DAA enable status |
| 0xC2000103 | MTK_SIP_LK_SBC_GET_SLA_ENABLE | Get SLA enable status |
| 0xC2000104 | MTK_SIP_LK_SBC_CHECK_PUBK_HASH | Check public key hash |
| 0xC2000105 | MTK_SIP_LK_ANTI_ROLLBACK | Anti-rollback check |
| 0xC2000106 | MTK_SIP_LK_DAPC_INIT | DEVAPC initialization |
| 0xC2000107 | MTK_SIP_LK_MD_REG_WRITE | Modem register write |
| 0xC2000108 | MTK_SIP_LK_AES256_INIT | AES-256 init |
| 0xC2000109 | MTK_SIP_LK_AES256_PROCESS | AES-256 encrypt/decrypt |
| 0xC200010A | MTK_SIP_LK_AES256_DONE | AES-256 finalize |
| 0xC200010B | MTK_SIP_LK_CRYPTO_SHA256_INIT | SHA-256 init |
| 0xC200010C | MTK_SIP_LK_CRYPTO_SHA256_PROCESS | SHA-256 update |
| 0xC200010D | MTK_SIP_LK_CRYPTO_SHA256_SKIP | SHA-256 skip |
| 0xC200010E | MTK_SIP_LK_CRYPTO_SHA256_PROCESS | SHA-256 process (alt) |
| 0xC200010F | MTK_SIP_LK_CRYPTO_SHA256_DONE | SHA-256 finalize |
| 0xC2000110 | MTK_SIP_LK_GET_RND | Get random number |
| 0xC2000111 | MTK_SIP_LK_IMGBUF_INIT | Image buffer init |
| 0xC2000112 | MTK_SIP_LK_IMGBUF_PROCESS | Image buffer process |
| 0xC2000113 | MTK_SIP_LK_IMGBUF_DONE | Image buffer done |
| 0xC2000114 | MTK_SIP_LK_MPU_PERM_SET | EMI MPU permission set |
| 0xC2000115 | MTK_SIP_LK_TINYSYS_EVENT | Tinysys event notification |
| 0xC2000116 | MTK_SIP_LK_VCORE_NOTIFY | Vcore notification |
| 0xC2000117 | MTK_SIP_LK_SET_MBLOCK_RULE | Memory block rule setup |
| 0xC2000118 | MTK_SIP_LK_GET_MBLOCK_RULE | Memory block rule query |
| 0xC2000119 | MTK_SIP_LK_CRYPTO_SHA384_INIT | SHA-384 init |
| 0xC200011A | MTK_SIP_LK_CRYPTO_SHA384_PROCESS | SHA-384 update |
| 0xC200011B | MTK_SIP_LK_CRYPTO_SHA384_DONE | SHA-384 finalize |
| 0xC200011C | MTK_SIP_LK_SECURE_FUNC | Secure function call |
| 0xC200011D | MTK_SIP_LK_BORINGSSL_INIT | BoringSSL init |
| 0xC200011E | MTK_SIP_LK_BORINGSSL_PROCESS | BoringSSL process |
| 0xC200011F | MTK_SIP_LK_BORINGSSL_DONE | BoringSSL done |
| 0xC2000120 | MTK_SIP_LK_TEE_RPMB_INIT | TEE RPMB init |
| 0xC2000121 | MTK_SIP_LK_SBOOT_INIT | Secure boot init |

### SMC Services Called by Kernel (23 services)

| SMC ID | Name | Description |
|--------|------|-------------|
| 0xC2000200 | MTK_SIP_KERNEL_APC_SET | DEVAPC set from kernel |
| 0xC2000201 | MTK_SIP_KERNEL_APC_SET2 | DEVAPC set (variant 2) |
| 0xC2000202 | MTK_SIP_KERNEL_DFD | DFD (Design For Debug) |
| 0xC2000203 | MTK_SIP_KERNEL_AMMS | AMMS (Adaptive Memory Management System) |
| 0xC2000204 | MTK_SIP_KERNEL_MPU_PERM_SET | EMI MPU permission |
| 0xC2000205 | MTK_SIP_KERNEL_EMIMPU | EMI MPU control |
| 0xC2000207 | MTK_SIP_KERNEL_GZ | GeniZone hypercall |
| 0xC2000208 | MTK_SIP_KERNEL_CCU | Camera Control Unit |
| 0xC2000209 | MTK_SIP_KERNEL_TINYSYS_EVENT | Tinysys event |
| 0xC200020A | MTK_SIP_KERNEL_SCP | SCP control |
| 0xC200020B | MTK_SIP_KERNEL_VCP | VCP control |
| 0xC200020C | MTK_SIP_KERNEL_GPUEB | GPU EB control |
| 0xC200020D | MTK_SIP_KERNEL_SPM | SPM control |
| 0xC200020E | MTK_SIP_KERNEL_SSPM | SSPM control |
| 0xC200020F | MTK_SIP_KERNEL_ADSP | ADSP control |
| 0xC2000210 | MTK_SIP_KERNEL_APUSYS | APUSYS control |
| 0xC2000211 | MTK_SIP_KERNEL_INFRA_MPU_SET | Infra MPU |
| 0xC2000212 | MTK_SIP_KERNEL_SPM_SMPU | SPM SMPU |
| 0xC2000213 | MTK_SIP_KERNEL_CONNSYS | ConnSys control |
| 0xC2000214 | MTK_SIP_KERNEL_DVFSRC | DVFSRC control |
| 0xC2000215 | MTK_SIP_KERNEL_AUDIO | Audio control |
| 0xC2000216 | MTK_SIP_KERNEL_CLK_BUF | Clock buffer |
| 0xC2000217 | MTK_SIP_KERNEL_SUSPEND | Suspend/resume |

### SMC Services for TEE (11 services)

| SMC ID | Name | Description |
|--------|------|-------------|
| 0xC2000300 | MTK_SIP_TEE_MPU_PERM_SET | TEE MPU permission |
| 0xC2000301 | MTK_SIP_TEE_EMIMPU | TEE EMI MPU |
| 0xC2000302 | MTK_SIP_TEE_RPMB_ACCESS | RPMB access from TEE |
| 0xC2000303 | MTK_SIP_TEE_SEC_DEINT | Secure de-init |
| 0xC2000304 | MTK_SIP_TEE_CRYPTO | TEE crypto operations |
| 0xC2000305 | MTK_SIP_TEE_SEC_IO | Secure I/O |
| 0xC2000306 | MTK_SIP_TEE_TRNG | True RNG access |
| 0xC2000307 | MTK_SIP_TEE_GZ | TEE-to-GeniZone |
| 0xC2000308 | MTK_SIP_TEE_GCPU | TEE GCPU crypto engine |
| 0xC2000309 | MTK_SIP_TEE_UFS | TEE UFS access |
| 0xC200030A | MTK_SIP_TEE_PMIC | TEE PMIC access |

### SMC Services for Hypervisor (14 services)

| SMC ID | Name | Description |
|--------|------|-------------|
| 0xC2000400 | MTK_SIP_HYP_MPU_PERM_SET | Hyp MPU permission |
| 0xC2000401 | MTK_SIP_HYP_EMIMPU | Hyp EMI MPU |
| 0xC2000402 | MTK_SIP_HYP_SEC_IO | Hyp secure I/O |
| 0xC2000403 | MTK_SIP_HYP_DEVAPC | Hyp DEVAPC |
| 0xC2000404 | MTK_SIP_HYP_CONNSYS | Hyp ConnSys |
| 0xC2000405 | MTK_SIP_HYP_CCU | Hyp CCU |
| 0xC2000406 | MTK_SIP_HYP_SCP | Hyp SCP |
| 0xC2000407 | MTK_SIP_HYP_VCP | Hyp VCP |
| 0xC2000408 | MTK_SIP_HYP_GPUEB | Hyp GPU EB |
| 0xC2000409 | MTK_SIP_HYP_SPM | Hyp SPM |
| 0xC200040A | MTK_SIP_HYP_SSPM | Hyp SSPM |
| 0xC200040B | MTK_SIP_HYP_ADSP | Hyp ADSP |
| 0xC200040C | MTK_SIP_HYP_APUSYS | Hyp APUSYS |
| 0xC200040D | MTK_SIP_HYP_AUDIO | Hyp Audio |

### Shared/Common SMC Services (11 services)

| SMC ID | Name | Description |
|--------|------|-------------|
| 0xC2000500 | MTK_SIP_COMMON_INFO | Common info query |
| 0xC2000501 | MTK_SIP_COMMON_EFUSE | eFuse read/write |
| 0xC2000502 | MTK_SIP_COMMON_PMIC | PMIC access |
| 0xC2000503 | MTK_SIP_COMMON_UFS | UFS access |
| 0xC2000504 | MTK_SIP_COMMON_RND | Random number |
| 0xC2000505 | MTK_SIP_COMMON_TRNG | True RNG |
| 0xC2000506 | MTK_SIP_COMMON_MD | Modem access |
| 0xC2000507 | MTK_SIP_COMMON_DVFSRC | DVFSRC |
| 0xC2000508 | MTK_SIP_COMMON_CLK_BUF | Clock buffer |
| 0xC2000509 | MTK_SIP_COMMON_TINYSYS | Tinysys |
| 0xC200050A | MTK_SIP_COMMON_MISC | Miscellaneous |

## Fastboot Unlock Confirmation

**Critical finding**: The ATF binary contains **zero references** to unlock, confirm, volume, button, fastboot, boot state, orange, green, or seccfg. The SMC `0xC200010F` (previously thought to be the unlock confirmation) actually maps to `MTK_SIP_LK_CRYPTO_SHA256_DONE`, a hardware SHA-256 finalization call.

The fastboot unlock confirmation mechanism resides in **MTEE (BL32)**, the TEE OS loaded separately by ATF. MTEE runs Trusted Applications (TAs) that can render a secure UI and read input devices. On the 557 device, the unlock confirmation TA waits for a volume key press, but the device uses ADC-based buttons (`singleadc-joypad`) that the TEE's GPIO/key driver cannot detect. This causes the 5-second timeout and automatic cancel.

The actual unlock flow is:
1. LK's fastboot handler receives `flashing unlock` command
2. LK calls into MTEE (via SMC to BL31, which routes to BL32) to display a secure confirmation screen
3. MTEE renders the "Unlock bootloader?" prompt in the secure framebuffer
4. MTEE polls for volume key input via its own GPIO driver
5. On 557, the ADC joypad is invisible to MTEE's driver, so no key press is detected
6. After 5 seconds, MTEE returns "cancel" to LK
7. LK reports unlock failure

This is why patching the LK unlock handler alone does not work. The confirmation happens at a higher privilege level (S-EL1) before LK's handler code runs.

## TEE OS: MediaTek MTEE

The TEE OS is MediaTek's proprietary MTEE, confirmed by memory region names:
- `MTEE_TEE_SHARED` - Shared memory between normal and secure worlds
- `MTEE_TEE_STATIC` - Static TEE memory region
- `SECURE_OS` - Secure OS reserved region

No strings for OP-TEE, Trusty, Kinibi, TEEGRIS, Microtrust, or Beanpod were found. MTEE is loaded as BL32 by the ATF and runs at S-EL1.

GeniZone hypervisor integration is present (`gz-tee-static-shm`), allowing MTEE to coexist with a hypervisor at EL2.

## Memory Protection

BL31 configures extensive memory protection across multiple hardware units:

### DEVAPC (Device Access Permission Control)

8 DEVAPC domains control peripheral bus access:

| Domain | Scope |
|--------|-------|
| Main DEVAPC | Primary SoC peripherals |
| Peripheral DEVAPC | I/O peripherals |
| VLP DEVAPC | Very Low Power domain |
| ADSP DEVAPC | Audio DSP subsystem |
| MMINFRA DEVAPC | Multimedia infrastructure |
| MMUP DEVAPC | Multimedia upper |
| GPU DEVAPC | GPU subsystem |
| APUSYS DEVAPC | AI Processing Unit |

Each domain defines which bus masters (AP, modem, TEE, hypervisor, coprocessors) can access which peripherals, enforced in hardware.

### EMI MPU (External Memory Interface Memory Protection Unit)

25+ memory protection regions enforce access control on DRAM:

| Region | Purpose |
|--------|---------|
| `AP_REGION` | Application processor |
| `MD_REGION` | Modem |
| `SECURE_OS` | TEE OS |
| `TF-A` | ATF code/data |
| `MTEE_TEE_SHARED` | TEE shared memory |
| `MTEE_TEE_STATIC` | TEE static memory |
| `SCP` / `VCP` | Coprocessor regions |
| `SSPM` / `ADSP` / `GPUEB` | Subsystem regions |
| `GZ` / `gz-tee-static-shm` | GeniZone hypervisor |
| `CONNINFRA` | Connectivity |
| `CCU` | Camera Control Unit |
| `AMMS_POS` | Adaptive Memory Management |

Additional protection via:
- **INFRA MPU**: Infrastructure bus protection
- **SPM SMPU**: System Power Manager memory protection
- **Sub-band MPU**: Fine-grained protection within regions

## Hardware Crypto Engine (TZCC)

BL31 provides secure-world access to hardware crypto accelerators:

| Feature | Description |
|---------|-------------|
| SHA-256 | Hardware-accelerated hashing (used by LK via SMC) |
| SHA-384 | Hardware-accelerated hashing |
| AES-256-CBC | Hardware-accelerated encryption/decryption |
| AES-256 Key Wrap | Key wrapping for secure key transport |
| TRNG | True Random Number Generator (hardware entropy) |
| GCPU | General-purpose crypto processing unit |
| SASI/CryptoCell | ARM CryptoCell integration for key management |

Key material managed:
- `RPMB KEY` - Replay Protected Memory Block key
- `UCUK` - Unique Customer Unique Key
- `BASE_KEY` - Base derivation key
- KDF (Key Derivation Function) for derived keys

## Root of Trust

BL31 implements several Root of Trust interfaces:

| HAL | Purpose |
|-----|---------|
| **ROT HAL** | Root of Trust - device identity and attestation |
| **StrongBox HAL** | Hardware-backed keystore (tamper-resistant) |
| **RKP HAL** | Remote Key Provisioning for Android |
| **MD pubk hash** | Modem public key hash verification |

These provide the secure foundation for Android's hardware-backed security features (Keymaster/KeyMint, device attestation, remote provisioning).

## Coprocessor Management

BL31 manages security aspects of multiple coprocessors via dedicated SMC handlers:

| Coprocessor | Normal World SMC | TEE SMC | Hyp SMC |
|-------------|-----------------|---------|---------|
| SCP (System Control) | 0xC200020A | - | 0xC2000406 |
| VCP (Video) | 0xC200020B | - | 0xC2000407 |
| GPUEB (GPU) | 0xC200020C | - | 0xC2000408 |
| SSPM (Power Mgr) | 0xC200020E | - | 0xC200040A |
| ADSP (Audio) | 0xC200020F | - | 0xC200040B |
| APUSYS (AI) | 0xC2000210 | - | 0xC200040C |
| CCU (Camera) | 0xC2000208 | - | 0xC2000405 |

For each coprocessor, BL31 handles:
- Memory region setup and protection
- Firmware authentication before boot
- Runtime access control via DEVAPC/MPU
- Crash dump data collection (via DFD SMC)

## Connectivity (ConnSys)

BL31 manages security for the wireless connectivity subsystem:

- **ConnInfra**: WiFi, Bluetooth, GPS, FM radio
- **ADIE6637**: Analog/Digital IC for RF frontend
- Dedicated memory regions and DEVAPC entries
- Kernel and hypervisor can both access ConnSys via SMC

## Power Management (PSCI)

BL31 implements PSCI (Power State Coordination Interface) for:

| Operation | Description |
|-----------|-------------|
| `CPU_ON` | Bring a CPU core online |
| `CPU_OFF` | Take a CPU core offline |
| `CPU_SUSPEND` | Suspend a CPU core (idle) |
| `SYSTEM_SUSPEND` | Full system suspend |
| `SYSTEM_RESET` | System reset |
| `SYSTEM_OFF` | System power off |

SPM (System Power Manager) firmware is loaded and managed by BL31:
- `spmfw` partition loaded at boot
- SPM controls deep idle and suspend states
- DVFSRC (Dynamic Voltage Frequency Scaling Resource Controller) integration
- PMIC MT6363 power rail control during suspend/resume

## Debug and Logging

BL31 outputs log messages via UART with severity prefixes:
- `NOTICE:` - Important informational messages
- `WARNING:` - Warning conditions
- `ERROR:` - Error conditions
- `INFO:` - General informational (may require debug build)

Key log messages:
```
NOTICE: BL31: v2.8(release):lts-v2.8.13-...
NOTICE: BL31: Built : ...
NOTICE: BL31: Entering BL31 at EL3
NOTICE: BL31: Preparing for EL3 exit to normal world
```

DFD (Design For Debug) support for crash analysis:
- MCU DFD: CPU core trace capture
- SoC DFD: System-level debug data
- GPU DFD: GPU subsystem debug
- LastPC capture for all CPU cores

## GeniZone Hypervisor Integration

BL31 supports MediaTek's GeniZone hypervisor running at EL2:
- Dedicated SMC services (0xC2000400 range) for hypervisor calls
- Shared memory regions: `gz-tee-static-shm`
- GeniZone can manage its own set of DEVAPC, MPU, and coprocessor access rules
- TEE-to-GeniZone communication via `MTK_SIP_TEE_GZ`

## Comparison with LK and Preloader

| Aspect | Preloader | LK (BL33) | TEE/ATF (BL31) |
|--------|-----------|-----------|----------------|
| **Exception Level** | EL3 (early), then EL1 | EL1 (NS) | EL3 (permanent) |
| **Runs in** | SRAM, then DRAM | DRAM (normal world) | DRAM (secure world) |
| **Primary job** | HW init, DRAM training | Boot mode, kernel load | Secure services, SMC routing |
| **Signing format** | SLA two-block | cert1/cert2 DER | cert1/cert2 DER (same as LK) |
| **Signing keys** | Same MTK test keys | Same MTK test keys | Same MTK test keys |
| **User interaction** | None | Display, fastboot, menu | None (headless) |
| **Persists at runtime** | No (replaced by LK) | No (replaced by kernel) | Yes (resident at EL3) |
| **Size** | ~775 KB | ~860 KB | ~400 KB |

## Source Code Structure

Embedded paths and strings reveal the TF-A codebase:

```
bl31/                            - BL31 entry and runtime
  bl31_main.c                    - Main initialization
  bl31_entrypoint.S              - Entry from BL2

plat/mediatek/mt6897/            - MT6897 platform code
  plat_sip_svc.c                 - SIP SMC service dispatch
  plat_topology.c                - CPU topology (4+3+1)
  plat_pm.c                      - Power management
  drivers/
    devapc/                      - Device Access Permission Control
    emi_mpu/                     - EMI Memory Protection Unit
    spm/                         - System Power Manager
    pmic/                        - PMIC control
    crypto/                      - TZCC hardware crypto
    dfd/                         - Design For Debug

lib/psci/                        - PSCI library
lib/el3_runtime/                 - EL3 runtime services
common/                          - TF-A common code
drivers/arm/gic/                 - GIC driver
```
