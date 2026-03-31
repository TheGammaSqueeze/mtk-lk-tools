# CPU/GPU Overclocking Analysis (MT6897 / Dimensity 8300)

Analysis of overclocking possibilities on the 557 device (Anbernic gaming console, MT6897 / Dimensity 8300), focusing on partitions we can modify and re-sign with the MTK test keys.

## Firmware Partition Signing

All firmware partitions use the same MTK test signing keys and can be re-signed with our tools:

| Partition | Size | Sub-partitions | Keys Match | Encrypted |
|-----------|------|---------------|------------|-----------|
| **mcupm_a** | 1 MB | tinysys-mcupm-RV33_A + xfile | YES | YES |
| **gpueb_a** | 2 MB | tinysys-gpueb-RV33_A + xfile | YES | YES |
| **sspm_a** | 2 MB | tinysys-sspm | YES | YES |
| **spmfw_a** | 1 MB | spmfw | YES | YES |
| **lk_a** | 16 MB | lk, bl2_ext, aee, lk_main_dtb, lk_dtbo | YES | NO |
| **preloader** | 4 MB | GFH preloader | YES | NO |
| **tee_a** | 5 MB | atf | YES | NO |

**Critical finding**: MCUPM and GPUEB firmware (which control CPU and GPU DVFS respectively) are **fully encrypted**. Both sub-partitions show near-maximum entropy (~7.96 bits/byte) with no meaningful strings. The firmware is encrypted at build time (likely with a hardware key via SoC secure boot). We can re-sign the partition container, but we cannot modify the encrypted firmware payload.

## CPU Overclocking

### Architecture

CPU frequency scaling on the MT6897 uses a co-processor-managed architecture:

```
Kernel cpufreq-hybrid driver
    |
    v
CSRAM tables (frequency/voltage pairs)
    |
    v
MCUPM firmware (RISC-V MCU, manages PLLs)
    |
    v
ARMPLL_LL / ARMPLL_BL / ARMPLL_B / CCIPLL (hardware PLLs)
```

The kernel reads CPU OPP tables from CSRAM (not from DTB). MCUPM firmware writes these tables to CSRAM during initialization and directly controls the PLL registers.

### CPU Topology

| Cluster | Cores | CPU | PLL | CSRAM Offset |
|---------|-------|-----|-----|-------------|
| Little | 4x cpu0-3 | Cortex-A510 | ARMPLL_LL (0x0c030400) | 0x04 |
| Mid | 3x cpu4-6 | Cortex-A715 | ARMPLL_BL (0x0c030800) | 0x4c |
| Big/Prime | 1x cpu7 | Cortex-A715 | ARMPLL_B (0x0c030c00) | 0x94 |
| CCI | - | Interconnect | CCIPLL (0x0c030000) | 0xdc |

### Where CPU Frequency Tables Live

| Location | What's There | Modifiable? |
|----------|-------------|-------------|
| **MCUPM firmware** (mcupm_a) | Encrypted RISC-V binary with internal OPP tables | NO (encrypted) |
| **CSRAM** (runtime) | Frequency/voltage tables written by MCUPM at boot | Not persistent (RAM) |
| **Preloader** | Initial PLL frequency arrays, segment tables | YES (can re-sign) |
| **Kernel DTB** (dtbo_a) | `cpufreq-hybrid` node with CSRAM table offsets | YES (can re-sign) |
| **Kernel cpufreq driver** | Reads CSRAM tables, controls DVFS policy | YES (can modify kernel) |

### Preloader CPU Frequency Arrays

The preloader contains segment-selectable CPU frequency arrays:

**ARMPLL_LL (Little cores)** at preloader offset 0xa85b4:
```
1200, 1300, 1500, 1700, 1800, 2000, 2100, 2200,
2700, 2800, 2900, 3000, 3100, 3300, 3400, 3500 MHz
```

**ARMPLL_BL (Mid cores)** at preloader offset 0xa8624:
```
1700, 1750, 1800, 1850, 1800, 1900, 2000, 2100, 2200, 2300,
2400, 2500, 2600, 2700, 2800, 2900, 3000, 3100, 3200, 3300 MHz
```

**ARMPLL_B (Big/Prime core)** at preloader offset 0xa8674:
```
600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700,
1800, 1900, 2000, 2100 MHz
```

**CCI** at preloader offset 0xa85f4:
```
550, 600, 650, 700, 750, 800, 900, 950, 1000, 1050, 1100, 1150 MHz
```

These are the max-frequency options per efuse segment. The efuse segment ID selects which entry is used. Modifying these in the preloader could raise the ceiling, but MCUPM firmware must also support the higher frequency.

### CPU Overclocking Feasibility

| Approach | Effort | Risk | Feasibility |
|----------|--------|------|-------------|
| Modify preloader frequency arrays | Low | Medium | Might work if MCUPM doesn't independently cap |
| Kernel cpufreq driver patch | Medium | Low | Best approach - modify max freq in kernel |
| Decrypt/modify MCUPM firmware | Very High | High | Comprehensive but requires decryption key |
| Direct PLL register writes from kernel | Low | High | MCUPM may fight back or crash |

**Recommended CPU approach**: Patch the kernel's cpufreq driver or the kernel DTB (in `dtbo_a` or `boot_a`) rather than trying to modify the encrypted MCUPM firmware. The kernel driver reads CSRAM tables and enforces its own frequency limits. The kernel image can be modified and re-signed via AVB.

## GPU Overclocking

### Architecture

GPU frequency scaling also uses a co-processor:

```
Mali kbase driver -> GED module -> SYSRAM -> GPUEB firmware -> MFGPLL
```

When `gpueb-support = 1` (which it is on this device), GPUEB firmware exclusively controls the GPU PLL. The kernel cannot directly set GPU frequency.

### GPU OPP Table (in lk_main_dtb)

The LK device tree contains **65 GPU OPP entries**:

| Entry | Frequency | Voltage |
|-------|-----------|---------|
| opp00 (max) | **1400 MHz** | **931.25 mV** |
| opp01 | 1383 MHz | 918.75 mV |
| opp02 | 1367 MHz | 918.75 mV |
| ... | ~16 MHz steps | ~6.25 mV steps |
| opp29 | 932 MHz | 750.00 mV |
| opp30 | 916 MHz | 750.00 mV |
| ... | ... | ... |
| opp64 (min) | **265 MHz** | **500.00 mV** |

GPU IP: Mali-G615 (Valhall) at `mali@13000000`
GPU PLLs: MFGPLL at `0x13fa0000`, MFGSCPLL at `0x13fa0c00`
Power: vgpu-supply + vsram-supply

### GPUEB Firmware Status

The GPUEB firmware (`tinysys-gpueb-RV33_A` + xfile) is **fully encrypted**, same as MCUPM. We cannot read or modify the internal DVFS tables.

However, GPUEB reads the GPU OPP table from **shared memory** during initialization:
1. The kernel `mtk_gpufreq_mt6897` driver reads the DTB OPP table
2. Driver writes OPP entries to 16 KB shared memory (`MEM_ID_GPUFREQ`)
3. GPUEB reads this shared memory via `CMD_INIT_SHARED_MEM`
4. GPUEB uses these OPP entries for DVFS decisions

This means **modifying the DTB OPP table may propagate to GPUEB**.

### GPU Power/Thermal Throttling

| Condition | Frequency Cap |
|-----------|--------------|
| Low battery level 0 | 1200 MHz |
| Low battery level 1 | 700 MHz |
| Low battery level 2 | 300 MHz |
| Over-current level 0 | 1200 MHz |
| Over-current level 1 | 700 MHz |
| Thermal critical | 119 C |

### PMIC Voltage Headroom

The GPU is powered by MT6363 vbuck regulators. The PMIC supports up to **1193.75 mV**, while the current max GPU voltage is **931.25 mV**. There is ~262 mV of voltage headroom for overclocking.

### GPU Overclocking Feasibility

| Approach | Effort | Risk | Feasibility |
|----------|--------|------|-------------|
| **Modify lk_main_dtb OPP table** | Low | Low | Most promising - changes propagate via shared memory |
| Kernel gpufreq driver patch | Medium | Low | Can override OPP table at runtime |
| Direct MFGPLL register writes | Low | High | GPUEB may fight back |
| Decrypt/modify GPUEB firmware | Very High | High | Comprehensive but requires decryption key |

**Recommended GPU approach**: Modify the GPU OPP table in `lk_main_dtb` to raise the max frequency and voltage. This is the easiest path since:
1. lk_main_dtb is unencrypted and in the LK image we can re-sign
2. The OPP table propagates to GPUEB via shared memory
3. PMIC has voltage headroom

To overclock GPU to e.g. 1500 MHz:
1. Unpack LK image with `lk-unpack`
2. Modify the DTB's `opp-table0` node: change opp00's `opp-hz` to 1500 MHz, adjust voltage
3. Repack and re-sign with `lk-repack` + `lk-resign`

## DRAM Overclocking

DRAM frequency tables exist in the preloader at offset 0xa2f58:

```
8533, 8533, 6400, 5500, 4100, 3094, 2133, 1866, 1600, 800 MHz (data rate)
```

The DRAM is LPDDR5X running at up to 8533 MHz. Modifying these values is extremely risky (wrong DRAM timing = no boot or data corruption). Not recommended.

## Summary

| Target | Best Approach | Partition | Encrypted | Feasibility |
|--------|-------------|-----------|-----------|-------------|
| **GPU** | Modify DTB OPP table | lk_a (lk_main_dtb) | No | HIGH |
| **CPU** | Kernel cpufreq driver / boot DTB | boot_a or dtbo_a | No | MEDIUM |
| **CPU** (alt) | Preloader freq arrays | preloader_a | No | MEDIUM |
| **DRAM** | Preloader freq table | preloader_a | No | LOW (risky) |
| **GPU** (alt) | GPUEB firmware | gpueb_a | YES | BLOCKED |
| **CPU** (alt) | MCUPM firmware | mcupm_a | YES | BLOCKED |

The GPU OPP table in `lk_main_dtb` is the most accessible overclocking target. CPU overclocking is best done through the kernel (boot image / DTB overlay) rather than through the encrypted MCUPM firmware.

## GPU Undervolting

Undervolting reduces power consumption and heat, allowing the GPU to sustain higher frequencies for longer before thermal throttling kicks in (119 C trip point). The GPU OPP table in `lk_main_dtb` directly controls the voltage-frequency curve.

### Stock Voltage Curve

The stock curve runs from 500 mV at 265 MHz to 931.25 mV at 1400 MHz, with 6.25 mV steps:

| Frequency | Stock Voltage | mV/MHz Ratio |
|-----------|--------------|-------------|
| 1400 MHz | 931.25 mV | 0.665 |
| 1200 MHz | 856.25 mV | 0.710 |
| 1000 MHz | 775.00 mV | 0.777 |
| 800 MHz | 706.25 mV | 0.883 |
| 600 MHz | 631.25 mV | 1.050 |
| 400 MHz | 556.25 mV | 1.380 |

The PMIC supports down to ~400 mV and up to 1193.75 mV, so there is headroom in both directions.

### Power Savings from Undervolting

Power is proportional to V^2 * f. Reducing voltage has a squared effect on power consumption:

| Offset | Voltage at 1400 MHz | Power Reduction | Impact |
|--------|-------------------|----------------|--------|
| -25 mV | 906.25 mV | ~5% | Conservative, safe for all chips |
| -50 mV | 881.25 mV | ~10% | Good balance of temps and stability |
| -75 mV | 856.25 mV | ~15% | Significant thermal improvement |
| -100 mV | 831.25 mV | ~20% | Aggressive, test thoroughly |

At higher frequencies the absolute power savings are larger. A -50 mV undervolt at 1400 MHz saves ~10% power, which translates directly to lower temperatures and longer sustained boost clocks before thermal throttling.

### Recommended Undervolt Profiles

**Conservative (-25 mV offset across entire curve):**
- Safe starting point, minimal risk of instability
- 1400 MHz: 931.25 -> 906.25 mV
- 1000 MHz: 775.00 -> 750.00 mV
- 600 MHz: 631.25 -> 606.25 mV

**Moderate (-50 mV offset):**
- Good balance of thermal improvement and stability
- 1400 MHz: 931.25 -> 881.25 mV
- 1000 MHz: 775.00 -> 725.00 mV
- 600 MHz: 631.25 -> 581.25 mV

**Aggressive (-75 mV offset):**
- Maximum thermal benefit, requires stability testing
- 1400 MHz: 931.25 -> 856.25 mV
- 1000 MHz: 775.00 -> 700.00 mV
- 600 MHz: 631.25 -> 556.25 mV

### How to Apply

The undervolt is applied by modifying the `opp-microvolt` values in the `opp-table0` node of `lk_main_dtb`:

1. Unpack the LK image: `./lk-unpack /mnt/c/557/lk_a -o work/`
2. Decompile the DTB: `dtc -I dtb -O dts work/lk_main_dtb/data.bin -o work/lk_main_dtb/data.dts`
3. Edit the OPP table in the .dts file: reduce each `opp-microvolt` value by the desired offset
4. Recompile: `dtc -I dts -O dtb work/lk_main_dtb/data.dts -o work/lk_main_dtb/data.bin`
5. Repack and re-sign: `./lk-repack work/ /mnt/c/557/lk_a -o lk_a_undervolted`
6. Flash: `fastboot flash lk lk_a_undervolted`

The same approach works for overvolting (to stabilize overclocks) or for creating a combined overclock + undervolt profile (e.g., raise max frequency to 1500 MHz while keeping the voltage curve moderate).

### Stability Notes

- Insufficient voltage causes GPU hangs, artifacts, or driver crashes (not permanent damage)
- Start with -25 mV and stress test (e.g., heavy 3D gaming) before going lower
- Lower frequencies are more tolerant of undervolt than higher ones
- If unstable, you can apply a non-uniform offset: larger reduction at low frequencies, smaller at high frequencies
- The GPUEB firmware may apply additional voltage margins on top of the OPP table values
- Silicon lottery means each chip has different minimum stable voltages

## Thermal Trip Points

### LK DTB Thermal Zones (lk_main_dtb)

The LK device tree defines **14 thermal zones**, all with identical trip points:

| Property | Value | Meaning |
|----------|-------|---------|
| temperature | 0x1d0d8 | 119,000 millidegrees = **119 C** |
| hysteresis | 0x7d0 | 2,000 millidegrees = **2 C** |
| type (zone 0) | critical | Hardware shutdown |
| type (zones 1-13) | passive | Software throttling |

The 14 zones cover all CPU cores (sensors 0-7), GPU zones, and SoC thermal sensors.

### DTBO Camera Thermal Zones

The DTBO adds 4 camera-specific thermal zones (camera0-camera4) with:
- temperature = 0x1d4c0 = 120,000 millidegrees = **120 C** (critical)

### GPU-Specific Throttling (in LK binary)

The LK binary contains a GPU 3.0 Limit Table with temperature-indexed frequency ceilings:
- `[Temper] [Ceiling] [I_STACK] [I_SRAM] [P_STACK]` format
- Temperature compensation: `GPU Temper Comp: %d (KHz or mV*100)` for both normal and high conditions
- Current ceiling/floor system with priority-based limiters

GPU power limiters (read from GPUEB shared memory, not directly modifiable in LK DTB):
- Low battery level 0: ~1200 MHz cap
- Low battery level 1: ~700 MHz cap
- Low battery level 2: ~300 MHz cap
- Battery over-current level 0: ~1200 MHz cap
- Battery over-current level 1: ~700 MHz cap

### Are Trip Points Adjustable?

**Yes, but at different levels:**

**LK DTB trip points (lk_main_dtb)**: These control thermal protection during the bootloader stage only. Modifiable by editing the DTB:
- Change `0x1d0d8` (119 C) to e.g., `0x1e848` (125 C) or `0x20f58` (135 C)
- This is the safety shutdown / passive throttle threshold during boot
- Affects LK, bl2_ext, and AEE crash recovery thermal behavior

**Kernel thermal framework**: The real thermal throttling that affects gaming performance lives in the kernel DTB (inside `boot_a` and `dtbo_a` partitions). The kernel has:
- Active cooling maps linking thermal zones to cpufreq/gpufreq cooling devices
- Multiple trip points per zone (not just a single critical trip)
- Configurable polling intervals
- Step-wise or power-allocator governors
- These are modifiable via the kernel DTB (which we can sign via AVB)

**GPUEB internal limits**: Battery OC, low battery, and PBM (Peak Bandwidth Manager) frequency caps are in the GPUEB firmware's shared memory configuration. These are read at runtime and are harder to modify (encrypted firmware).

### Recommended Thermal Modifications

For a gaming console, the LK DTB's 119 C is already quite high. The more impactful changes are:

1. **GPU undervolt** (most effective): Lower voltage = less heat = longer sustained boost. Modify the OPP table in lk_main_dtb.

2. **Kernel thermal policy** (in boot_a/dtbo_a): Adjust the active cooling trip points and step-wise throttling to be less aggressive. This is where the gaming performance throttling actually happens.

3. **LK DTB trip point raise** (minor impact): Only affects boot-stage thermal, not runtime gaming. Raising from 119 C to 125 C gives slightly more headroom but the kernel's thermal framework kicks in well before that.
