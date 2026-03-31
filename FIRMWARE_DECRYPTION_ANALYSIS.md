# Firmware Decryption Bypass Analysis (MT6897 / Dimensity 8300)

Analysis of how bl2_ext loads and decrypts coprocessor firmware (MCUPM, GPUEB, SSPM, etc.) on the 557 device (Anbernic gaming console), and how to bypass decryption to load custom plaintext firmware.

## Background

Several critical firmware partitions (MCUPM for CPU DVFS, GPUEB for GPU DVFS, SSPM, SPMFW) are encrypted with AES-256-CBC using hardware-derived keys. The firmware is decrypted at load time by bl2_ext before being written to coprocessor SRAM. Since bl2_ext is in the LK image that we can re-sign, we can patch the decryption flow.

## Firmware Loading Pipeline

bl2_ext loads firmware images through this pipeline (source: `platform/mediatek/common/loader/load_image.c`):

```
load_image() at 0x0402a4
  |
  +-> Read partition header (0x200 bytes)
  +-> Parse mkimg header
  +-> sec_img_auth_init()     -- init auth context
  +-> sec_img_auth()          -- full cert chain verification
  +-> sec_img_check_enc()     -- always returns 0 (no-op)
  +-> sec_img_header_auth()   -- header authentication
  +-> sec_img_image_auth()    -- image hash verification
  +-> sec_img_decode()        -- DECRYPTION HAPPENS HERE
  +-> sec_img_finalize()      -- cleanup
```

Authentication and decryption are **separate steps**. You can bypass decryption without affecting authentication, or vice versa.

## Decryption Decision Flow

### sec_decode_pipeline() at bl2_ext offset 0x05575c

```
LDR w8, [x23, #0xC04]    ; load enc_type from auth context
CBZ w8, skip_decode        ; if enc_type == 0, skip decryption entirely
... (call decode_partition)
```

The `enc_type` field is extracted from the image's certificate/header metadata. If it's 0, decryption is skipped entirely.

### decode_partition() at bl2_ext offset 0x04e814

Simple dispatcher based on enc_type:

| enc_type | Action |
|----------|--------|
| 0 | Return immediately (plaintext) |
| 1 | Call crypto_dec_dispatch() for AES decryption |
| other | Return error 0x00101023 |

### crypto_dec_dispatch() at 0x048cb8

Calls `get_crypto_mode()` to select the crypto backend:

| Mode | Backend | Used on 557? |
|------|---------|-------------|
| 1 | **HACC hardware decrypt** (direct register I/O at 0x10040000) | **YES** (hardcoded) |
| 2 | TFA SMC decrypt (SMC 0xC2000133 to ATF) | No |

`get_crypto_mode()` at offset `0x00c95c` is **hardcoded to return 1**:
```
0x00c95c: MOV w0, #1
0x00c960: RET
```

### HACC Hardware Decrypt (Mode 1) at 0x05038c

1. Read key wrapper type from auth context (+0x14)
2. If wrapper type = 0: direct decrypt via `hacc_decrypt_core()`
3. If wrapper type = 1: unwrap key first via `key_unwrap()`, then decrypt
4. `hacc_decrypt_core()` at 0x0547fc configures the HACC engine at physical address `0x10040000`
5. HACC uses hardware key ladder via `SMC 0xC200040D` for key setup
6. Actual AES block cipher done via memory-mapped HACC register I/O

**The AES key is derived from hardware eFuse values via the HACC key ladder. The key never exists in readable software memory.** This is why re-encryption is not feasible - we don't have access to the key.

## Bypass Methods

### Method A: Patch decode_partition() (Recommended)

The simplest and most universal patch. Make `decode_partition()` always return success without decrypting:

**Location**: bl2_ext offset `0x04e814`

| | Bytes | Instruction |
|---|---|---|
| **Original** | `21 01 00 34` | `CBZ w1, +0x24` |
| **Patch** | `00 00 80 52 C0 03 5F D6` | `MOV w0, #0; RET` |

This 8-byte patch makes ALL firmware images skip decryption while still passing through authentication (cert chain verify, header auth, image hash check).

### Method B: Set enc_type=0 in Certificate

When re-signing a firmware partition with our tools, set the `enc_type` field to 0 in the cert2 certificate. bl2_ext reads this field at `ctx+0xC04` and skips decryption when it's 0.

This requires understanding which cert2 OID or field carries the enc_type. The field is populated during `sec_img_auth()` from the certificate metadata.

### Method C: Patch get_crypto_mode() to Return 0

At offset `0x00c95c`, change `MOV w0, #1` to `MOV w0, #0`:

| | Bytes | Instruction |
|---|---|---|
| **Original** | `20 00 80 52` | `MOV w0, #1` |
| **Patch** | `00 00 80 52` | `MOV w0, #0` |

This makes `crypto_dec_dispatch()` take the "no crypto" path. Less reliable than Method A because it still enters the decode flow.

## Complete Workflow: Custom Coprocessor Firmware

### Step 1: Dump Decrypted Firmware

The decrypted firmware runs from coprocessor SRAM at runtime. Dump it from a rooted device:

| Coprocessor | SRAM Base | Size | Purpose |
|-------------|-----------|------|---------|
| GPUEB | 0x13c00000 | 192 KB | GPU DVFS tables |
| MCUPM | (see DTB) | ~256 KB | CPU DVFS tables |
| SSPM | (see DTB) | ~256 KB | Power management |

From a rooted kernel:
```c
// Example kernel module to dump GPUEB SRAM
void __iomem *base = ioremap(0x13c00000, 0x30000);
// Read and save to file
```

Or via `/dev/mem` if available:
```bash
dd if=/dev/mem bs=1 skip=$((0x13c00000)) count=$((192*1024)) of=gpueb_decrypted.bin
```

### Step 2: Modify the Firmware

With the decrypted RISC-V binary, find and modify DVFS tables (frequency/voltage arrays).

### Step 3: Patch bl2_ext

Apply the decode_partition() patch (Method A) to the bl2_ext sub-partition in the LK image:

```bash
# Unpack LK
./lk-unpack /mnt/c/557/lk_a -o work/

# Patch bl2_ext data at offset 0x04e814
python3 -c "
d = bytearray(open('work/bl2_ext/data.bin','rb').read())
# Verify original bytes
assert d[0x4e814:0x4e818] == bytes.fromhex('21010034'), 'Unexpected bytes at patch site'
# MOV w0, #0; RET
d[0x4e814:0x4e81c] = bytes.fromhex('00008052c0035fd6')
open('work/bl2_ext/data.bin','wb').write(d)
"

# Repack and re-sign LK
./lk-repack work/ /mnt/c/557/lk_a -o lk_a_nodecrypt.img
```

### Step 4: Replace Encrypted Firmware with Plaintext

Replace the encrypted sub-partition data in the firmware partition (e.g., gpueb_a) with the modified plaintext version, then re-sign:

```bash
# Unpack the firmware partition
./lk-unpack /mnt/c/557/1.26/unbrick/gpueb_a -o gpueb_work/

# Replace encrypted data with plaintext modified version
cp gpueb_modified.bin gpueb_work/tinysys-gpueb-RV33_A/data.bin

# Repack and re-sign
./lk-repack gpueb_work/ /mnt/c/557/1.26/unbrick/gpueb_a -o gpueb_a_custom.img
```

### Step 5: Flash Both

```bash
fastboot flash lk lk_a_nodecrypt.img
fastboot flash gpueb_a gpueb_a_custom.img
```

## Why Re-encryption Is Not Feasible

The AES-256 key is derived via the HACC hardware key ladder:
1. HACC reads base keys from eFuse (hardware-only, not software-readable)
2. Key derivation uses `SMC 0xC200040D` which runs entirely in ATF at EL3
3. The derived key is loaded directly into the HACC engine registers
4. The key is **never exposed to software** - HACC does encrypt/decrypt internally
5. Key wrapping (MRSA/CSEC/CMCC formats) adds additional layers

Even with full EL3 access, the base eFuse keys are read by hardware, not by software instructions. There is no practical way to extract the encryption key.

**The bypass approach (loading plaintext firmware) is the correct solution.** It avoids the need for re-encryption entirely.

## Security Implications

- Authentication (cert chain + hash) still works after the decryption bypass
- The firmware partitions still need valid cert1/cert2 signatures (which we can provide with MTK test keys)
- The bypass only affects the confidentiality of the firmware, not its integrity verification
- On this device, `sbc_en=0` means image authentication is not enforced anyway, but maintaining valid signatures is good practice

## Key Offsets Reference

| bl2_ext Offset | Function | Purpose |
|----------------|----------|---------|
| 0x0402a4 | `load_image()` | Main image loading entry point |
| 0x05575c | `sec_decode_pipeline()` | Decryption decision (reads enc_type) |
| 0x04e814 | `decode_partition()` | **PATCH TARGET** - dispatch by enc_type |
| 0x048cb8 | `crypto_dec_dispatch()` | Selects HACC vs TFA crypto backend |
| 0x00c95c | `get_crypto_mode()` | Returns 1 (HACC mode, hardcoded) |
| 0x05038c | HACC decrypt setup | Key unwrap + HACC config |
| 0x0547fc | `hacc_decrypt_core()` | AES block cipher via HACC registers |
| 0x05d344 | `hacc_engine()` | Low-level HACC register I/O |
| 0x05c940 | `key_unwrap()` | Key wrapper decode (MRSA/CSEC/CMCC) |
| 0x048d1c | TFA SMC decrypt path | SMC 0xC2000133 (not used on this device) |

## SMC IDs Used in Crypto

| SMC ID | Function | Description |
|--------|----------|-------------|
| 0xC200010B | crypto_hw_tfa_init | Init hardware crypto via TFA |
| 0xC200010D | sha256_tfa_init | SHA-256 init via TFA |
| 0xC200010E | sha256_tfa_process | SHA-256 update via TFA |
| 0xC200010F | sha256_tfa_done | SHA-256 finalize via TFA |
| 0xC2000133 | aes256_cbc_dec_fw | AES-256-CBC decrypt (Mode 2, unused) |
| 0xC200040D | HACC operations | Hardware key ladder (Mode 1, active) |
