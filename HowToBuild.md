# HowToBuild.md

This document is the exact build and flash procedure for this repository.
It is written so another AI or engineer can execute the workflow without
guessing.

Current printer IP used in the commands below:

```bash
192.168.178.95
```

## What this repo builds

This repo can produce three different artifacts:

1. `firmware/firmware.bin`
   - full Snapmaker UPFILE package
   - use this for `systemUpgrade.sh upgrade all`
   - contains SoC image (`update.img`)
   - contains mainboard MCU payload (`at32f403a.bin`)
   - currently keeps stock toolhead MCU payloads unless you explicitly replace
     them
2. `tmp/firmware/update.img`
   - SoC-only image
   - use this for `systemUpgrade.sh upgrade soc`
3. `out_at32f403a/at32f403a.bin`
   - mainboard MCU-only image
   - use this for `systemUpgrade.sh upgrade mcu0`

## Critical rule: build order matters

If you want the SoC image and the full `firmware.bin` package to carry the
current custom mainboard MCU build, you must build the mainboard MCU first.

Reason:
- the SoC image build reads `out_at32f403a/at32f403a.bin`
- if that file exists, the build injects it into:
  - `/home/lava/firmware_MCU/at32f403a.bin`
  - the top-level UPFILE main-MCU payload
- if that file is stale or missing, the SoC/full package may contain the wrong
  MCU payload

Do not switch branches and then immediately build `firmware.bin` without first
rebuilding `out_at32f403a/at32f403a.bin` for that branch.

## Prerequisites

### Required for SoC / full firmware build

- Docker Desktop or another working Docker daemon
- an ARM64-capable Docker environment
- internet access for the first official firmware download

Docker is used only through `./dev.sh`.

`./dev.sh` does two things:
- builds the local dev container from `.github/dev/Dockerfile`
- runs the requested command inside that container with the repo mounted into it

You do not manually enter Docker for the normal build workflow.

### Required for MCU build

The MCU build runs on the host machine, not in Docker.

Required tools in `PATH`:
- `arm-none-eabi-gcc`
- `arm-none-eabi-as`
- `arm-none-eabi-ld`
- `arm-none-eabi-objcopy`
- `arm-none-eabi-objdump`
- `arm-none-eabi-strip`
- `arm-none-eabi-cpp`

Recommended but currently optional:
- `readelf`

Note:
- on macOS, the current MCU build may print `readelf: command not found`
- that warning has been non-fatal in this repo
- the MCU binary was still produced successfully

## Files and directories that matter

- official downloaded stock firmware:
  - `firmware/U1_1.2.0.106_20260323113459_upgrade.bin`
- full output package:
  - `firmware/firmware.bin`
- SoC output:
  - `tmp/firmware/update.img`
- mainboard MCU output:
  - `out_at32f403a/at32f403a.bin`
- temporary image workdir:
  - `tmp/firmware/`

## Clean build procedure

Run every command from the repo root:

```bash
cd /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper
```

### 1. Switch to the branch you actually want to build

Example:

```bash
git switch motor-phase-xDirect
```

or:

```bash
git switch argo-extended
```

Verify:

```bash
git branch --show-current
```

### 2. Remove stale branch-dependent outputs

This is important after a branch switch.

```bash
rm -rf out out_at32f403a out_at32f415
rm -f .config .config.old
rm -f firmware/firmware.bin
```

Why:
- `out/` and `.config` are MCU-build products from the previous target
- `out_at32f403a/at32f403a.bin` is consumed by the SoC/full-image build
- `firmware/firmware.bin` must be removed before rebuilding, because the image
  packer refuses to overwrite it

## Exact build procedure

### Step A. Build helper tools in Docker

```bash
./dev.sh make tools
```

### Step B. Download the stock Snapmaker firmware in Docker

```bash
./dev.sh make firmware
```

This downloads:

```bash
firmware/U1_1.2.0.106_20260323113459_upgrade.bin
```

### Step C. Build the mainboard MCU locally

This is the exact command sequence for the mainboard MCU:

```bash
cp -f lava/at32f403a_config .config
make CPP=arm-none-eabi-cpp clean
make CPP=arm-none-eabi-cpp
mkdir -p out_at32f403a
cp -f out/klipper.bin out_at32f403a/at32f403a.bin
```

Important:
- do not use `install -D` on macOS for this copy step
- BSD `install` syntax differs from GNU `install`
- `mkdir -p ... && cp -f ...` is the safe command here

### Step D. Build the SoC image and full package in Docker

After the mainboard MCU artifact exists, build the image:

```bash
./dev.sh make build PROFILE=extended
```

This produces:

```bash
firmware/firmware.bin
tmp/firmware/update.img
tmp/firmware/rootfs/
```

## Optional: build the toolhead MCU locally

Only do this if you explicitly need a separate toolhead MCU binary.

```bash
cp -f lava/at32f415_config .config
make CPP=arm-none-eabi-cpp clean
make CPP=arm-none-eabi-cpp
mkdir -p out_at32f415
cp -f out/klipper.bin out_at32f415/at32f415.bin
```

Output:

```bash
out_at32f415/at32f415.bin
```

Important:
- the normal SoC/full-image build path in this repo currently auto-injects the
  local mainboard MCU binary
- it does not automatically replace toolhead MCU payloads with a locally built
  `out_at32f415/at32f415.bin`

## Artifact verification

Hash verification is mandatory before flashing.

Do not flash any artifact from this repo unless you have computed and checked
its hash first.

Why this matters:
- `firmware.bin` is the full update package and can overwrite SoC + MCU payloads
- `update.img` overwrites the SoC image
- `at32f403a.bin` overwrites the mainboard MCU
- a truncated or wrong-branch artifact can waste hours or soft-brick the device

Minimum rule:
- always compute the hash immediately after the build
- record the hash together with the branch name
- verify the file you are about to copy/flash, not just an older file with the
  same name

After a successful build, compute hashes:

```bash
shasum -a 256 firmware/firmware.bin tmp/firmware/update.img out_at32f403a/at32f403a.bin
```

If the toolhead MCU was built too:

```bash
shasum -a 256 out_at32f415/at32f415.bin
```

Recommended workflow:

1. build artifacts
2. compute SHA-256 locally
3. copy artifact to the printer
4. if you need extra paranoia, compute the hash again on the copied file if the
   target system has `sha256sum` or `shasum`
5. only then run `systemUpgrade.sh upgrade ...`

If you already know the expected hash for a release candidate, verify it
explicitly:

```bash
echo "<expected_sha256>  firmware/firmware.bin" | shasum -a 256 -c
echo "<expected_sha256>  tmp/firmware/update.img" | shasum -a 256 -c
echo "<expected_sha256>  out_at32f403a/at32f403a.bin" | shasum -a 256 -c
```

## Flashing commands

All commands below target this printer:

```bash
192.168.178.95
```

### Flash SoC only

```bash
scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/tmp/firmware/update.img root@192.168.178.95:/tmp/
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
```

### Flash mainboard MCU only

```bash
scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/out_at32f403a/at32f403a.bin root@192.168.178.95:/tmp/
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade mcu0 /tmp/at32f403a.bin
```

### Flash a toolhead MCU only

Example for `head0`:

```bash
scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/out_at32f415/at32f415.bin root@192.168.178.95:/tmp/
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade head0 /tmp/at32f415.bin
```

Other toolheads:
- `head1`
- `head2`
- `head3`

### Flash the complete firmware package

This is the full fallback or full rollout path:

```bash
scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/firmware/firmware.bin root@192.168.178.95:/tmp/upgrade.bin
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade all /tmp/upgrade.bin
```

## Recommended post-flash actions

### After `upgrade soc`

```bash
ssh root@192.168.178.95 reboot
```

### After `upgrade mcu0`

```bash
ssh root@192.168.178.95 reboot
```

### After `upgrade all`

The printer may reboot on its own, but this is the explicit verification step:

```bash
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh show-status
```

## Status verification

Use this after any flash:

```bash
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh show-status
```

Interpretation for the current `motor-phase-xDirect` development line:
- `Main MCU` should report the custom local build
- `Print head0..3 MCU` may remain on stock if you did not explicitly rebuild and
  flash them

## Known pitfalls

### 1. `firmware/firmware.bin` already exists

Symptom:
- `./dev.sh make build PROFILE=extended` fails during repack with:
  - `Error: Output file .../firmware/firmware.bin already exists.`

Fix:

```bash
rm -f firmware/firmware.bin
./dev.sh make build PROFILE=extended
```

### 2. Wrong MCU bundled into SoC/full image

Cause:
- branch switched
- `out_at32f403a/at32f403a.bin` was not rebuilt

Fix:
- delete `out/`, `out_at32f403a/`, `.config`, `.config.old`
- rebuild the MCU first
- only then build `update.img` / `firmware.bin`

### 3. `readelf: command not found` during MCU build

Current status:
- warning only
- not observed to block creation of `out/klipper.bin`

Recommended fix:
- install a toolchain package that also provides `readelf`

### 4. Docker cache importer warning

Symptom:
- `failed to configure registry cache importer`

Current status:
- this has been observed as non-fatal if the build continues afterward

### 6. Linker exclusion of new source files

Symptom:
- `mcu 'mcu': Unknown command: motor_phase_exec_start` (or similar)
- `Compiling out/src/new_file.o` was visible in the build log
- But the commands defined in that file are missing from the binary

Cause:
- The Klipper build system uses LTO (Link Time Optimization) and `--gc-sections`.
- If no function in a new source file is explicitly called by the core firmware, the linker may discard the entire object file, including its `DECL_COMMAND` declarations.

Fix:
- Every new source file must contain at least one `DECL_INIT(func_name)` or another macro that registers a symbol in a linker section used by the core.
- Even an empty dummy function is enough:
  ```c
  void my_feature_init(void) { }
  DECL_INIT(my_feature_init);
  ```

Verification:
- Always verify that your new commands are actually in the binary before flashing:
  ```bash
  strings out/klipper.bin | grep your_command_name
  ```
- If this returns empty, the file was linked out.

## Minimal idiot-proof sequences

### Build everything for the current branch

```bash
cd /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper
git branch --show-current
rm -rf out out_at32f403a out_at32f415
rm -f .config .config.old firmware/firmware.bin
./dev.sh make tools
./dev.sh make firmware
cp -f lava/at32f403a_config .config
make CPP=arm-none-eabi-cpp clean
make CPP=arm-none-eabi-cpp
mkdir -p out_at32f403a
cp -f out/klipper.bin out_at32f403a/at32f403a.bin
./dev.sh make build PROFILE=extended
shasum -a 256 firmware/firmware.bin tmp/firmware/update.img out_at32f403a/at32f403a.bin
```

### Flash SoC + mainboard MCU for development

```bash
scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/tmp/firmware/update.img root@192.168.178.95:/tmp/
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/out_at32f403a/at32f403a.bin root@192.168.178.95:/tmp/
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade mcu0 /tmp/at32f403a.bin
ssh root@192.168.178.95 reboot
```

### Flash full fallback package

```bash
scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/firmware/firmware.bin root@192.168.178.95:/tmp/upgrade.bin
ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade all /tmp/upgrade.bin
```
