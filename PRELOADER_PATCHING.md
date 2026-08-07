# Preloader Patching - MT6897 (557 / Anbernic)

Guide for modifying the MTK preloader binary on MT6897 devices. Covers the AND_ROMINFO_v patch for DA bypass, the re-signing workflow, and what can and cannot be patched.

## Background

The preloader runs from SRAM immediately after the Boot ROM (BROM). It is the first mutable software stage in the boot chain and controls:

- DRAM initialization
- Storage initialization (UFS)
- Security setup (eFuse reading, RPMB key management)
- Loading and authenticating the LK
- Download mode (DA loading and execution)

Because the preloader is signed with the MTK test keys (same key pair as LK images), and because `sbc_en=0` on the 557, we can modify and re-sign the preloader and it will be accepted by the BROM.

## AND_ROMINFO_v Patch (DA Bypass)

### What AND_ROMINFO_v Is

`AND_ROMINFO_v` is a magic value embedded in the preloader's GFH header chain. It is a structured security metadata block that the Download Agent (DA) reads when it starts up. The DA uses this block to determine what security policies to enforce during flash operations, including:

- Whether to require signature verification on each partition write
- Which partitions are write-protected
- What lock state is in effect

### Where It Is

In a UFS_BOOT preloader (like the 557's `preloader_a.bin`), the `AND_ROMINFO_v` magic is located at offset **0x1288** from the start of the file. It occupies 16 bytes.

The GFH header chain starts at the BRLYT's `boot_region_addr` (typically `0x1000` for UFS_BOOT containers). The `AND_ROMINFO_v` block is one of the GFH headers in this chain, starting at `0x1288` (which is `0x288` bytes into the GFH chain, i.e., after several other GFH headers).

For EMMC_BOOT containers, the offset may differ. Use a hex editor to search for the literal string `AND_ROMINFO_v` to find it.

### What Zeroing It Does

The DA loads the preloader from flash and searches for the `AND_ROMINFO_v` magic to read the security metadata. If the magic is zeroed (all `0x00`), the DA cannot find a valid ROMINFO block and falls back to a permissive mode that allows unrestricted partition writes via SP Flash Tool or mtkclient.

This is the most reliable way to enable full DA-level flash access on devices where the DA normally rejects partition writes, without needing to modify the DA itself (which we cannot re-sign due to the Xiaomi key).

### Limitations

This patch makes the preloader's AND_ROMINFO region invalid. The BROM and the preloader itself do not use this field for their own operation, only the DA reads it. Booting into Android is unaffected. The only effect is that DA-based flashing (SP Flash Tool, mtkclient DA mode) becomes unrestricted.

## Patching Workflow

### Method 1: Python one-liner

```bash
python3 -c "
d = bytearray(open('preloader_a.bin','rb').read())
d[0x1288:0x1298] = b'\x00' * 16
open('preloader_a_patched.bin','wb').write(d)
print('Patched: AND_ROMINFO_v zeroed at 0x1288')
"
```

### Method 2: preloader_patch.py script

Use the included `preloader_patch.py` script which handles offset detection automatically:

```bash
# Patch AND_ROMINFO_v
./preloader-patch preloader_a.bin --zero-rominfo -o preloader_a_patched.bin

# Show what would be patched (dry run)
./preloader-patch preloader_a.bin --zero-rominfo --info

# Verify the patch was applied
./preloader-patch preloader_a_patched.bin --info
```

### Method 3: Hex editor

Open the preloader in a hex editor, go to offset `0x1288`, and overwrite 16 bytes with `00`.

Verify: search for the string `AND_ROMINFO_v` - it should no longer appear in the file.

### Re-sign After Patching

After any modification, the preloader must be re-signed before it will boot. The GFH `file_len` records the exact size of the signed content region; any change to the binary (even a single byte) invalidates Block 2.

```bash
./preloader-resign preloader_a_patched.bin -o /tmp/preloader_a_final.bin
```

Always write to a Linux filesystem path first (e.g., `/tmp/`), then copy to the destination if needed.

Verify the re-signing:

```bash
./preloader-resign /tmp/preloader_a_final.bin --verify
# Should show: Block2 (image sig): OK
```

Block 1 is never modified by `preloader-resign`. It contains only key binding data (root key and image key moduli), which is independent of the preloader binary content. Only Block 2 (the image hash and signature) needs to be updated.

### Flash the Patched Preloader

```bash
fastboot flash preloader_a /tmp/preloader_a_final.bin
fastboot flash preloader_b /tmp/preloader_a_final.bin
```

Or with SP Flash Tool if fastboot is not available: use scatter file targeting the `preloader` partition.

After flashing, the device boots normally. The only change is that DA-based flash operations are no longer restricted by the AND_ROMINFO security policy.

## Signing Algorithm Details

The preloader uses RSA-PSS with SHA-256 and a **fixed salt length of 32 bytes**. This is different from LK image signing (which uses salt_length=-1/auto/max).

Using the wrong salt length produces a signature that verifies correctly with OpenSSL but is rejected by the MTK BROM during preloader authentication. Always use `salt_length=32` for preloader signing.

The `preloader-resign` tool uses `salt_length=32` automatically. The equivalent manual OpenSSL command:

```bash
openssl dgst -sha256 \
    -sigopt rsa_padding_mode:pss \
    -sigopt rsa_pss_saltlen:32 \
    -sign keys/img_prvk.pem \
    -out sig.bin \
    tbs.bin
```

## GFH Header Layout Reference

The preloader's signed region begins at the GFH offset (0x1000 for UFS_BOOT). The GFH headers at the start of the chain, up to and including the AND_ROMINFO block:

```
Offset  Header type          Notes
------  -----------          -----
0x1000  GFH_FILE_INFO        file_len, sig_len, sig_type=5, load_addr=0x02000F00
0x1038  GFH_BL_INFO          bootloader type, attributes
0x1050  GFH_BROM_SEC_CFG     BROM security config (eFuse override fields)
0x1090  GFH_ARM_BL_INFO      ARM-specific BL info
0x10B0  GFH_JUMP_BL_INFO     jump table and entry info
...
0x1288  AND_ROMINFO_v block   Security metadata read by DA (16 bytes magic + data)
```

The exact layout depends on firmware version. The offsets above are from the 557's factory preloader. Use `./preloader-resign preloader.bin --info` to see the parsed GFH structure and verify the file_len/sig_len boundaries.

## What Cannot Be Changed

### DA Authentication (daa_en=1)

The 557 has `daa_en=1` burned in eFuse. The DA image itself (`DA_BR.bin`) is signed with Xiaomi's private key, not the MTK test keys. We cannot re-sign the DA. The AND_ROMINFO patch works around this by making the DA operate in permissive mode, but the DA file itself is immutable.

### BROM Behavior

The BROM is mask ROM - it cannot be modified. It reads its own embedded key hash to verify the preloader's Block 1. Because the 557 uses `sbc_en=0`, this verification is not enforced in practice (the BROM loads the preloader regardless of the signature check result), but we still need a valid signature structure so the BROM can parse the GFH headers correctly.

### DRAM Training Data

The preloader contains calibrated DRAM training parameters specific to the device. Do not modify DRAM controller registers or PLL configuration inside the preloader binary unless you have replacement calibration data. An incorrect DRAM init will cause the device to hang at boot or show DRAM errors.

## Related Documents

- [PRELOADER_ANALYSIS.md](PRELOADER_ANALYSIS.md) - Full preloader structure and boot flow analysis
- [DA_ANALYSIS.md](DA_ANALYSIS.md) - Download Agent analysis, why it rejects images, AND_ROMINFO role
- [DEVICE_557.md](DEVICE_557.md) - 557 eFuse configuration and security model
- [BOOTLOADER_UNLOCK.md](BOOTLOADER_UNLOCK.md) - LK patching for bootloader unlock
