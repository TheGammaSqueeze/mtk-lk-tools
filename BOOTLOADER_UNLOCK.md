# LK Bootloader Unlock - MT6897 (557 / Anbernic)

Complete guide for unlocking the bootloader on devices using the MTK LK image with the standard MTK test signing keys, with specific notes for the 557 (Anbernic gaming console, MT6897 / Dimensity 8300).

## Background

"Unlocking the bootloader" on MTK Android devices sets the device to **orange state** in the AVB (Android Verified Boot) chain. In orange state:

- The LK passes `androidboot.verifiedbootstate=orange` to the kernel
- dm-verity enforcement is disabled (or reports unlocked status)
- The LK shows a warning screen on each boot ("custom OS is not subject to the same testing")
- Fastboot gains full flash access (no partition write restrictions from the lock state check)

On most MTK devices, bootloader unlock is gated behind:

1. `is_unlock_allowed` / `unlock_ability` checks in the LK
2. A TEE-driven 5-second confirmation screen where the user must press Volume Up to confirm
3. Writing the unlock bit to `seccfg` or RPMB on confirmation

On the 557 specifically, step 2 is broken by hardware: the TEE handles the confirmation via SMC `0xC200010F`, but the device uses ADC-multiplexed joystick buttons (`singleadc-joypad`) that the TEE cannot read. The TEE cannot detect any key press, so the confirmation always times out and auto-selects "cancel". Running `fastboot flashing unlock` on stock firmware shows the confirmation screen and then immediately cancels.

The solution is to patch the LK binary directly so the device reports itself as always-unlocked, bypassing the confirmation flow entirely.

## Why Binary Patching Works on the 557

The 557's eFuse configuration:

- `sbc_en=0` - Secure Boot Controller is **not** enabled. The preloader loads the LK without verifying its signature against eFuse-burned keys
- `sla_en=0` - No SLA challenge-response
- `daa_en=1` - DA authentication is active, but this only affects Download Agent, not LK

Because `sbc_en=0`, the cert chain in the LK image is verified by `bl2_ext` using its internal verification (not an eFuse root of trust), and that internal verification uses the MTK test keys. We have the private side of those test keys. So: patch the LK binary, re-sign with `lk-resign`, flash. The device will boot the modified LK.

## Patch Categories

Four patch categories are needed for a full unlock:

### fastboot

Patches the function that checks whether the device is currently unlocked (reads the unlock bit from `oplusreserve` or `seccfg`). The function is patched to always return `0` (unlocked), so the LK treats the device as already-unlocked on every boot without ever needing to write the unlock bit.

This is the core bypass for the 557's TEE/ADC-button problem: instead of going through the confirmation flow, we just make the LK believe it was already unlocked.

Needle / replacement pairs (Thumb-2 ARM):

| Needle | Replacement | Notes |
|--------|-------------|-------|
| `2de9f04fadf5ac5d` | `00207047` | PUSH {r4-fp,lr} STRD r5,r6... -> MOVS r0,#0 BX lr |
| `f0b5adf5925d` | `00207047` | PUSH {r4-r7,lr} SUB sp... -> MOVS r0,#0 BX lr |

### dm_verity

Patches the function that returns the current vbmeta/dm-verity verification state. Returning `0` means "no verity issues", suppressing the dm-verity warning on boot and preventing kernel verity mount failures.

| Needle | Replacement |
|--------|-------------|
| `30b583b002ab0022` | `00207047` |

### orange_state

Patches the function that checks the current LCS (Life Cycle State) to determine if a "device is unlocked" warning should be displayed. Returning `0` suppresses the orange-state warning screen on each boot. This is cosmetic if you already applied the `fastboot` patch, but including it avoids the warning screen even when things are in a mixed state.

| Needle | Replacement |
|--------|-------------|
| `08b50a4b7b441b681b68022b` | `00207047` |
| `08b50e4b7b441b681b68022b` | `00207047` |

### red_state

Patches the function that prints the "device verification failure" warning to return immediately, so red-state output is suppressed. Cosmetic on a device where verification is not enforced, but prevents confusing log output.

| Needle | Replacement |
|--------|-------------|
| `f0b5002489b0` | `00207047` |

In all cases the replacement is `00 20 70 47`: `MOVS r0, #0` + `BX LR` in Thumb-2, making the function unconditionally return zero and exit.

## Tool: lkpatcher

The [lkpatcher](https://github.com/R0rt1z2/lkpatcher) tool (by Roger Ortiz / R0rt1z2) applies these patches to the raw `lk` sub-partition data inside an MTK LK image. It searches for the needle byte sequences and replaces them with the patch bytes. Because LK images are re-signed after patching, the tool only needs to modify the binary data; `lk-resign` handles the cert2 hash update and signature.

Install:

```bash
pip install lkpatcher
```

Or clone and install from source:

```bash
git clone https://github.com/R0rt1z2/lkpatcher
cd lkpatcher
pip install -e .
```

The bundled `patches.json` matches the default patches above:

```json
{
    "fastboot": {
        "2de9f04fadf5ac5d": "00207047",
        "f0b5adf5925d": "00207047"
    },
    "dm_verity": {
        "30b583b002ab0022": "00207047"
    },
    "orange_state": {
        "08b50a4b7b441b681b68022b": "00207047",
        "08b50e4b7b441b681b68022b": "00207047"
    },
    "red_state": {
        "f0b5002489b0": "00207047"
    }
}
```

## Full Unlock Workflow

### Step 1: Extract the lk partition data

Either use `lk-unpack` to extract the `lk` sub-partition, or work on the full LK image directly. lkpatcher operates on the sub-partition binary, not the full LK image.

```bash
./lk-unpack lk_a.img -o lk_unpacked/
# Produces lk_unpacked/lk/data.bin (the raw LK code)
```

### Step 2: Apply patches with lkpatcher

```bash
python3 -m lkpatcher lk_unpacked/lk/data.bin -o lk_unpacked/lk/data_patched.bin
```

Or with a custom patches file:

```bash
python3 -m lkpatcher lk_unpacked/lk/data.bin -j patches.json -o lk_unpacked/lk/data_patched.bin
```

lkpatcher will print which needles were found and applied. On a matching firmware, all four categories should apply at least one needle each. If a needle is not found, it is skipped (lkpatcher continues by default).

### Step 3: Replace the patched data

```bash
cp lk_unpacked/lk/data_patched.bin lk_unpacked/lk/data.bin
```

### Step 4: Repack

```bash
./lk-repack lk_unpacked/ lk_a.img -o /tmp/lk_patched.img
```

Always write the output to a Linux filesystem path (`/tmp/`), not to a Windows/NTFS mount. See the NTFS note in the README.

### Step 5: Re-sign

`lk-repack` calls `lk-resign` internally, but run it explicitly to confirm:

```bash
./lk-resign /tmp/lk_patched.img
```

Check that the `lk` sub-partition shows `re-signed, verified=OK`. Other sub-partitions (bl2_ext, aee, lk_main_dtb, lk_dtbo) should show `hashes OK, preserving original signature` because their content was not changed.

### Step 6: Flash

```bash
# Copy from /tmp to wherever fastboot can find it
cp /tmp/lk_patched.img /path/to/flash/lk_patched.img

# Flash to both slots
fastboot flash lk_a lk_patched.img
fastboot flash lk_b lk_patched.img

# Also flash lk_t (if present - recovery/backup LK slot)
fastboot flash lk_t lk_patched.img
```

### Step 7: Verify

Reboot to fastboot and check:

```bash
fastboot getvar unlocked
# Should return: unlocked: yes

fastboot getvar secured
# Should return: secured: no  (or similar indicating unlocked)
```

The device should boot into Android without showing a red or orange state warning (because `orange_state` and `red_state` patches suppress the warnings), and fastboot flash should now accept writes to all partitions.

## Patching Alternative: Direct fastboot unlock (if confirmation screen works)

If you are on a device where the TEE CAN detect button presses (not the 557):

```bash
fastboot flashing unlock
# Press Volume Up within 5 seconds on the confirmation screen
```

This writes the unlock bit to `seccfg`. LK on subsequent boots reads the bit, reports orange state, and enables flash access. No LK patching required.

On the 557 this does not work due to the ADC button issue described above.

## Re-lock

To re-lock (restore stock lock state), flash an unpatched, factory-signed LK image. The device will return to green-state boot. You can also run `fastboot flashing lock` on a patched LK, though the `seccfg` lock state is largely irrelevant if the LK is patched to ignore it.

## Signature Caching Note

Some MTK devices cache cert2 signature bytes in `seccfg` or RPMB. If you re-sign the LK and the device rejects it despite a valid RSA-PSS signature, the device is comparing the new (different) signature bytes against a cached copy. In this case:

- Use `lk-resign` in its default mode (only re-signs partitions whose hashes actually changed). The `lk` sub-partition will get a new signature since you changed its data. The other sub-partitions (bl2_ext, aee, lk_main_dtb, lk_dtbo) will keep their original factory signatures, which avoids triggering the cache mismatch on those partitions.
- Alternatively, construct a hybrid image that uses the original factory cert2 for all sub-partitions except `lk`. This is more complex but can help if even a single re-signed cert2 triggers the caching issue on your specific device.

The 557 with `sbc_en=0` does not appear to have this issue in practice - re-signed preloaders and LK images both boot fine.

## Related Documents

- [LK_ANALYSIS.md](LK_ANALYSIS.md) - Full LK image structure, sub-partitions, and boot flow
- [PRELOADER_PATCHING.md](PRELOADER_PATCHING.md) - Preloader modification for DA bypass
- [DEVICE_557.md](DEVICE_557.md) - 557-specific eFuse config and security model
- [DA_ANALYSIS.md](DA_ANALYSIS.md) - Download Agent analysis and why it rejects images
