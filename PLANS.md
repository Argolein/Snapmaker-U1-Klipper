# PLANS.md

## Objective
Import the selected paxx12 Extended Firmware changes into this official Snapmaker U1 Klipper repo so that this repo becomes the working source of truth for:

1. Snapmaker U1 host and MCU sources
2. building a flashable Snapmaker-style `upgrade.bin` image
3. flashing that image to the printer with the selected community features included

Scope for this work:
- only the 7 selected features
- plus the minimal support layers and external dependencies required to make those 7 features work correctly

End state:
- this repo can build the firmware image
- the built image is flashable
- the selected features are owned and reproducible from this repo

---

## Architecture

### Validated facts
- This repo already contains Snapmaker U1-specific Klipper host and MCU sources and configs.
- The Extended Firmware repo provides a working image-builder pipeline:
  1. download official firmware
  2. unpack Snapmaker/Rockchip container
  3. extract squashfs rootfs
  4. apply overlays
  5. rebuild squashfs
  6. repack firmware image
- The required builder infrastructure exists in `SnapmakerU1-Extended-Firmware`:
  - `tools/rk2918_tools/`
  - `tools/upfile/`
  - `tools/resource_tool/`
  - `scripts/create_firmware.sh`
  - `scripts/extract_squashfs.sh`
  - `scripts/helpers/`
  - `.github/dev/Dockerfile`
  - `vars.mk`
  - `dev.sh`
- `create_firmware.sh` requires root and relies on `unsquashfs`, `mksquashfs`, `patch`, `file`, `wget`, `git`, and helper scripts.
- The current Extended Firmware build environment is Debian Trixie ARM64 in Docker.
- The Remote Screen and Camera features are not standalone. They depend on support layers outside their main overlays.
- Klipper wildcard includes like `[include extended/klipper/*.cfg]` do allow an empty match set. Keep files are optional for parser behavior, but still useful as placeholders.

### Target architecture
- This repo becomes the source of truth for the selected feature set.
- The image build pipeline is ported into this repo.
- The base image still starts from the official Snapmaker firmware download.
- The selected paxx12 features are imported into this repo as repo-owned overlays, patches, scripts, and support files.
- The resulting image must reflect files owned by this repo, not only stock firmware plus runtime patches.
- To achieve that, this repo needs a dedicated host-sync overlay that copies the repo-owned host-side Klipper payload into the image where applicable, instead of relying only on the stock `/home/lava/klipper` from the downloaded firmware.

### Allowed support layers
The user approved importing the minimal support layers required by the chosen features:
- `02-firmware-config` subset
- `common/04-nginx-fluidd.d`
- `common/05-persist-dhcp`
- USB NIC firmware blobs from commit `ccde0b8`

---

## Open questions
- None currently blocking.

---

## Decisions
- **Output**: flashable Snapmaker U1 `upgrade.bin` image built from this repo
- **Base image**: official Snapmaker firmware downloaded from Snapmaker CDN
- **Repo role**: this repo must own both the imported feature implementation and the image build logic
- **Host integration**: host-side files from this repo must be injected into the firmware image via repo-owned overlays where needed
- **Support layers**: minimal required support overlays may be imported when necessary to make the 7 selected features work
- **USB NIC compatibility**: include the USB NIC firmware blob overlay from `ccde0b8`
- **Build environment**: Docker-based ARM64 development environment, following the Extended Firmware model
- **Feature 3 includes**: placeholder keep files may be kept for clarity, but they are not required to avoid a Klipper wildcard-include error
- **Camera build model**: `v4l2-mpp` is not a mystery prebuilt binary; the current Extended Firmware builds it from a pinned external git repository during image build
- **Motor phase runtime gate**: the current printer-side `H2/H4` execution-near gate (`selected_score mean ~0.876`) is good enough to justify the next runtime-nearer prototype, but it is still not a benefit claim
- **Rejected motor phase paths**: both the host-`dwell()` direct-mode path and the pre-scheduled SPI direct-mode path are rejected as credible benefit-test paths
- **Next motor phase architecture step**: the next credible runtime step is a dedicated CoreXY-aware executor on the mainboard MCU near the executed-step path, not another host/SPI sequencing refinement
- **Next motor phase executor scope**: baseline first, but with coherent `stepper_x + stepper_y` carriage-space motion; apply the frozen correction profile to the selected target motor only after baseline smoothness is proven
- **TMC2240 DIRECT_MODE register format**: register 0x2D; coil_a bits[8:0] 9-bit signed (−255..+255), coil_b bits[24:16] 9-bit signed (−255..+255); SPI write is 5 bytes [0xAD, MSB..LSB]
- **MCU executor ISR constraint**: spidev_transfer() is blocking; executor timer ISR only sets pending flag and wakes task; DECL_TASK does the actual SPI write
- **Baseline table location**: ideal sin/cos table (1024 entries) lives in MCU flash as compile-time const; no host upload needed for the baseline-only Phase 3 prototype
- **Executor SPI OID**: executor uses its own `config_motor_phase_exec`-registered spidev_s OID, not the existing TMC2240 SPI config OID
- **MCU version checks must stay enabled**: the real fix for the slow reboot/recovery loop is a split version contract (`VERSION_MAIN` for `mcu0`, `VERSION_HEAD` for `head0..head3`), not `/oem/.skip_checking_mcu`
- **Boot-loop root cause**: `systemUpgrade.sh` originally used one global `/home/lava/firmware_MCU/VERSION` for both the custom main MCU and the stock head MCUs, so `check-restore` kept treating the unchanged heads as mismatched during boot
- **Canonical build/flash doc**: `HowToBuild.md` is the authoritative exact procedure for branch switching, MCU-first builds, Docker-based SoC builds, and flash commands to the U1 at `192.168.178.95`
- **CoreXY executor constraint**: on the U1, `stepper_x` and `stepper_y` share `enable_pin: PB2`, so a single-motor direct-mode executor will fight a still-energized partner motor unless the partner is explicitly released or both motors are driven coherently
- **CoreXY motion model**: for a pure carriage `Y` move on U1 CoreXY, `stepper_x` and `stepper_y` must run with opposite phase advance signs; for a pure carriage `X` move they run with the same sign
- **Executor sign bug**: the MCU executor must store `phase_advance` as `int16_t`; the earlier `uint8_t` field turned host-side `-1` into `255`, which made the dual-CoreXY baseline buzz instead of rotate
- **Executor start discipline**: dual CoreXY executors now need a shared absolute `start_clock` on the main MCU; starting `stepper_x` and `stepper_y` sequentially without a common timebase leaves them time-skewed even when their signs are correct
- **Direct-mode polarity rule**: the dual CoreXY executor must include each motor's real `dir_pin` inversion when deriving `phase_advance`; unlike normal Step/Dir motion, XDIRECT has no hardware direction pin, so ignoring `get_dir_inverted()` makes `stepper_x` and `stepper_y` fight each other on U1
- **U1 cross-motor phase rule**: the U1 CoreXY pair rests about 256 logical phase units apart in direct mode (`MSCURACT`/`MSCNT` observation), so the partner executor channel needs a fixed `exec_partner_phase_offset: 256`; without it the dual baseline can start quietly but still produce balanced torque instead of visible carriage travel
- **Klipper executor ramp rule**: for U1 XDIRECT the MCU executor needs both a frequency ramp and a coil-amplitude ramp; with only frequency ramp the dual CoreXY baseline can still trip `uv_cp`/`vm_uvlo` when the paired motors finally line up and demand real launch torque
- **Basis-calibration escape hatch**: because U1 direct-mode still has motor-specific unknowns beyond pure timing (partner phase basis and possible coil mapping), `MOTOR_PHASE_EXEC_RUN` now needs host-side runtime overrides for the partner channel (`PARTNER_PHASE_OFFSET`, `PARTNER_SWAP_COILS`, `PARTNER_INVERT_A`, `PARTNER_INVERT_B`) so electrical basis alignment can be solved systematically without reflashing for every trial
- **First stable dual-executor baseline**: U1 now has a crash-free visible-movement baseline with `PARTNER_PHASE_OFFSET=216`, `PARTNER_INVERT_B=1`, `COIL_SCALE=120`, and reduced executor current (`EXEC_IRUN_PCT=50`). Remaining artifact is a light `thumb thumb` at the end, which points to stop/exit shaping rather than startup or mapping failure
- **Executor stop/restore handshake**: a fixed post-stop sleep is not sufficient on U1. The host must not restore `direct_mode` / `IHOLD_IRUN` until the MCU executor explicitly reports `MPE_IDLE`, otherwise a run can end with a late TMC GSTAT reset even after visible motion succeeded
- **Executor safe-exit rule**: `MPE_IDLE` alone is not enough if the MCU reaches it before all zero-current writes are drained. The current fix therefore adds an MCU-side `MPE_ZERO_HOLD -> MPE_DRAIN -> MPE_IDLE` tail and a host-side explicit `coil_a=0/coil_b=0` teardown before `direct_mode=0`
- **Current blocker location**: the latest hardware log shows the present crash occurs about two seconds after `synchronized_start`, before teardown. The immediate bottleneck is therefore still the start/ramp/cruise motion law, not the new safe-exit tail.
- **Current ramp policy**: the active executor build now uses a much softer launch (`interval_target * 16`, slower interval ramp, slower amplitude ramp, lower minimum start scale) to test whether the remaining U1 fault is simply an over-aggressive mid-run launch profile.
- **Current debug method**: mid-run failures are now debugged with executor telemetry, not blind parameter changes. The MCU exposes buffer depth, overflow count, event/transfer counters, and last transmitted phase/scale via `mpe_query`; the host polls and logs these snapshots during `MOTOR_PHASE_EXEC_RUN`.
- **Current telemetry conclusion**: the first telemetry run showed healthy queue/SPI behavior all the way into `MPE_CRUISE` (`depth=0`, `max_depth=1`, `overflow_count=0`, `event_count ~= transfer_count`) before shutdown. The current blocker is therefore sustained electrical cruise load, not MCU backlog.
- **Launch vs. cruise policy**: `MOTOR_PHASE_EXEC_RUN` now supports `PRIME_COIL_SCALE` so launch torque can stay high while steady-state `COIL_SCALE` is reduced. This is the next principled correction after telemetry proved the scheduler is not the bottleneck.
- **Breakaway vs. cruise policy**: the next control-law slice separates three regimes:
  - `PRIME_COIL_SCALE` for pre-start vector seeding
  - `BREAKAWAY_COIL_SCALE` for a short post-start high-torque window
  - `COIL_SCALE` for sustained cruise
  This is required because U1 now has evidence for a stable-but-stationary cruise window (`48`) and an unstable-but-moving window (`64`).
- **Newest hardware result**: the first breakaway retest completed fully with
  `COIL_SCALE=48`, `BREAKAWAY_COIL_SCALE=64`, `BREAKAWAY_MS=750`,
  `SPEED=0.5`, `DISTANCE=10` and no crash, but still without visible carriage
  motion. That means the current cruise point is stable and the breakaway
  window is still too weak to produce net carriage travel.
- **Auto-calibration root-cause**: the previous `MOTOR_PHASE_AUTO_CALIBRATE`
  ran at COIL_SCALE=64/EXEC_IRUN_PCT=40 which produced near-zero accelerometer
  signal across all 32 tests. The "best" result (offset=704) was noise, not
  real motor motion. All subsequent tests at 704 and the regression at 216
  were therefore at insufficient current (100/45 vs the known-working 120/50).
- **Architecture redesign (2026-04-08)**: full calibration rewrite to use
  a StealthChop-inspired closed-loop approach:
  1. `MOTOR_PHASE_DIRECTION_PROBE` — single-motor test (partner at zero current)
     at high COIL_SCALE (default 120) to verify individual motor direction
     before any partner scan; uses total-magnitude accelerometer scoring
     so diagonal motion is detected regardless of CoreXY axis angle.
  2. Redesigned `MOTOR_PHASE_AUTO_CALIBRATE` — now runs direction probe first,
     then coarse scan at detection-grade current (default COIL_SCALE=100,
     SPEED=2.0, DURATION=0.20s, PHASE_STEP=32 → 64 tests), followed by
     optional fine scan (PHASE_STEP//4) around the best coarse result; adds
     SETTLE_TIME (default 0.5s) between bursts and REHOME_INTERVAL (default 8)
     for carriage-drift prevention.
- **Breakaway decay bug**: the first `BREAKAWAY_COIL_SCALE=80` hardware run
  exposed a real MCU logic bug: once the executor reached `MPE_CRUISE`, the
  generic ramp-up clause still re-incremented `coil_scale_current` toward the
  breakaway target every event. That effectively pinned cruise near `79/80`
  instead of tapering to `COIL_SCALE=48`. The fix is to disable that upward
  ramp once the executor is already in `MPE_CRUISE`.
- **Post-fix hardware verdict**: the corrected `BREAKAWAY_COIL_SCALE=80` run
  now drops cleanly to `COIL_SCALE=48` right after cruise entry and completes
  without crash, but still produces no visible carriage motion. The remaining
  blocker is therefore no longer breakaway decay; it is the underlying
  force/phase model for net CoreXY carriage travel.
- **CoreXY electrical-basis correction**: the observed idle `MSCNT` difference
  between `stepper_x` and `stepper_y` is not itself proof that the two motors
  need a fixed cross-motor electrical phase offset. These are independent
  motors; each must primarily stay aligned to its own runtime current vector.
  The default executor config should therefore return to `exec_partner_phase_offset: 0`,
  with partner offsets retained only as an explicit debug override.
- **Host debug-path constraint**: runtime telemetry during `MOTOR_PHASE_EXEC_RUN`
  must not use `toolhead.dwell()`; that needlessly perturbs the host-side
  motion stack and can make Fluidd appear unresponsive. The monitor loop now
  uses `reactor.pause()` with a slower 1.0 s cadence.

---

## Validated dependencies and prerequisites

### Build-time infrastructure
- Docker
- root privileges inside the build environment
- `SYS_ADMIN` capability for squashfs/chroot operations
- Builder tools from the Extended Firmware repo

### Packages installed by the current Extended Firmware dev image
- `build-essential`
- `cmake`
- `pkg-config`
- `squashfs-tools`
- `git-core`
- `bc`
- `flex`
- `bison`
- `libssl-dev`
- `dos2unix`
- `sudo`
- `sshpass`
- `unzip`
- `wget`
- `g++-aarch64-linux-gnu`
- `gcc-aarch64-linux-gnu`
- `file`
- `golang-go`
- `ffmpeg`
- `u-boot-tools`
- `ccache`
- `libssl-dev:arm64`

### External sources currently used by the selected features
- official Snapmaker firmware download from `vars.mk`
- `deps/screen-apps/` for Remote Screen
- `https://github.com/paxx12/v4l2-mpp.git` pinned in the camera overlay script
- static curl release archive from `stunnel/static-curl`

### Required support layers identified during validation
- `02-firmware-config` subset:
  - `01-add-klipper-includes.patch`
  - `01-add-moonraker-includes.patch`
  - `extended-config.py`
  - `extended/extended.cfg`
  - keep/config seed files under `extended/klipper/` and `extended/moonraker/`
- `common/04-nginx-fluidd.d`
  - required because Remote Screen now installs `remote-screen.conf` and `auth-check.conf` under `/etc/nginx/fluidd.d/`
- `common/05-persist-dhcp`
  - required so DHCP lease state survives reboots for USB ethernet
- `common/07-persist-ssh-hostkeys`
  - required so enabling official SSH on the printer keeps a stable host key across reboots
- `ccde0b8`
  - required for broader USB NIC compatibility

### Architecture/runtime constraints
- The current builder uses `chroot` into the extracted ARM64 rootfs and executes commands there.
- Remote Screen currently runs `pip3 install` inside the ARM64 rootfs during build.
- That means the Docker build environment must actually be able to execute ARM64 binaries in the chroot.
- The Extended Firmware documentation assumes an ARM64 Docker environment. Running the same process on x86_64 would require additional emulation/binfmt work that is not yet part of the approved plan.

---

## Feature inventory

### Feature 1 — OEM Disk Usage
**Source commit**: `858b4bf`

**Validated implementation**:
- `overlays/firmware-extended/12-patch-moonraker/patches/01-add-oem-disk-usage-support.patch`

**Risk**: Medium
- Moonraker failure affects web UI/API, but not basic printer firmware operation

### Feature 2 — Remote Screen
**Source commit**: `d1b033e`

**Validated current implementation**:
- overlay now lives conceptually in `61-app-remote-screen`
- depends on:
  - `deps/screen-apps/`
  - `common/04-nginx-fluidd.d`
  - `02-firmware-config` subset
- validated files in current Extended repo include:
  - `/etc/init.d/S99fb-http`
  - `/etc/nginx/fluidd.d/remote-screen.conf`
  - `/etc/nginx/fluidd.d/auth-check.conf`
  - `/usr/local/share/fb-http/html/*`
  - `extended/moonraker/04_remote_screen.cfg`

**Important correction**:
- this is no longer just a direct `printer.cfg` edit plus a standalone Python script
- current implementation uses include-driven config plus nginx sidecar config files

**Risk**: Medium
- nginx or auth integration errors can break the web UI

### Feature 3 — Extended config directory
**Validated implementation path**:
- `02-firmware-config/patches/01-add-klipper-includes.patch`
- `02-firmware-config/patches/01-add-moonraker-includes.patch`
- keep files:
  - `extended/klipper/00_keep.cfg`
  - `extended/moonraker/00_keep.cfg`

**Important correction**:
- wildcard includes do not require a non-empty stub to avoid parser failure
- keep files remain useful as explicit placeholders and seed content

**Risk**: Low

### Feature 4 — Add curl
**Source commit**: `9fd81e0`

**Validated current implementation**:
- `01-system-utils/scripts/curl_install.sh`
- current validated SHA256:
  - `3c6562544e1a21cd37e9dec7c48c7a6d9a2f64da42fde69ba79e54014b911abb`

**Important correction**:
- the SHA256 previously recorded in this plan was incorrect

**Risk**: Low

### Feature 5 — Disable IPv6
**Source commit**: `557e30a`

**Validated historical implementation**:
- standalone overlay adding `/etc/sysctl.d/90-disable-ipv6.conf`

**Risk**: Low

### Feature 6 — Camera FPS improvements
**Validated source commits**:
1. `e5c588a` — Fluidd camera feed base
2. `26103d7` — USB camera support
3. `0534964` — v4l2-mpp with WebRTC
4. `34e9d9f` — use WebRTC for camera
5. `7d53571` — fix WebRTC overlay
6. `3c409bf` — USB camera fixes and reconnect/error handling
7. `fb69f54` — update `capture-v4l2-raw|jpeg-mpp`
8. `07cb908` — WebRTC and RTSP resilience
9. `d6518b6` — retry support

**Validated current implementation dependencies**:
- `60-app-camera/scripts/01-v4l2-mpp.sh`
- `60-app-camera/scripts/02-extra-apps.sh`
- `60-app-camera/root/etc/init.d/S99v4l2-mpp-mipi`
- `60-app-camera/root/etc/init.d/S99v4l2-mpp-usb`
- `60-app-camera/root/etc/udev/rules.d/99-v4l2-mpp-usb.rules`
- `60-app-camera/root/usr/local/share/firmware-config/extended/moonraker/02_internal_camera.cfg`
- `60-app-camera/root/usr/local/share/firmware-config/extended/moonraker/03_usb_camera.cfg`
- external git dependency:
  - `https://github.com/paxx12/v4l2-mpp.git`
  - pinned at `10fc3b9d935d9c79bacc014839c05de4a004c4ac` in the current script

**Important correction**:
- this is not blocked on “is there a precompiled binary?”
- it is blocked only by integration complexity and external build dependency management

**Risk**: High

### Feature 7 — USB Ethernet
**Validated source commits**:
1. `d98529d` — eth0 base support
2. `7087081` — DHCP on USB ethernet
3. `8d45307` — improved eth0 support
4. `8487185` — udev rules for eth0 and USB camera
5. `546d577` — persist random eth0 MAC
6. `ccde0b8` — common USB NIC firmware blobs

**Validated support dependency**:
- `common/05-persist-dhcp`

**Risk**: Medium-High
- network misconfiguration can impact connectivity and recovery workflows

---

## File-level change list

### Image-builder infrastructure to import
- `tools/rk2918_tools/`
- `tools/upfile/`
- `tools/resource_tool/`
- `scripts/create_firmware.sh`
- `scripts/extract_squashfs.sh`
- `scripts/helpers/`
- `.github/dev/Dockerfile`
- `vars.mk`
- `dev.sh`

### New repo areas expected in this repo
- `overlays/`
- `firmware/` during builds
- `tmp/` during builds
- `deps/screen-apps/`

### Existing repo files expected to change
- `Makefile`
  - add image-builder targets without breaking current MCU build targets
- possibly README/build docs
  - document image build flow and prerequisites

### Repo-owned host integration expected
- add a dedicated overlay that syncs repo-owned host files into the firmware image
- likely source areas:
  - `klippy/`
  - `lava/`
  - built `klippy/chelper`
  - any additional host scripts/config files required for the selected features

---

## Approved plan

### Phase 0 — Plan validation
1. Validate Claude's findings against this repo and `SnapmakerU1-Extended-Firmware`
2. Correct `PLANS.md`
3. Record missing dependencies and support overlays

**Acceptance checks**:
- plan no longer contains the wildcard-include error claim
- support overlay dependencies are explicit
- architecture is consistent with the repo-owning-the-result goal

### Phase 1 — Port image-builder foundation
1. Import builder tools, scripts, helper scripts, Dockerfile, `vars.mk`, and `dev.sh`
2. Extend the existing `Makefile` with image-builder targets:
   - `tools`
   - `firmware`
   - `build`
   - `extract`
3. Ensure current MCU build flow is not broken by the new targets/names
4. Port `deps/screen-apps/`
5. Add baseline `overlays/` structure

**Acceptance checks**:
- image-builder targets run from this repo
- `make firmware` downloads and verifies the official image
- `make extract` unpacks the image successfully
- `make tools` builds the imported image tools successfully

### Phase 2 — Port support layers required by the selected features
1. Import the minimal `02-firmware-config` subset
2. Import `common/04-nginx-fluidd.d`
3. Import `common/05-persist-dhcp`
4. Import USB NIC firmware blob support from `ccde0b8`
5. Add a repo-owned host-sync overlay so the image can include files from this repo where required

**Acceptance checks**:
- extracted rootfs contains:
  - include hooks for `extended/klipper/*.cfg`
  - include hooks for `extended/moonraker/*.cfg`
  - nginx `fluidd.d` include support
  - DHCP persistence init script
  - USB NIC firmware support files

### Phase 3 — Implement low-risk features
1. Feature 3: extended include support
2. Feature 5: disable IPv6
3. Feature 4: curl

**Acceptance checks**:
- extracted rootfs shows the expected files and config hooks
- curl binary is present and executable in the image rootfs
- IPv6 sysctl file is present

### Phase 4 — Implement medium-risk features
1. Feature 1: OEM Disk Usage
2. Feature 7: USB Ethernet, including:
   - eth0 config
   - DHCP persistence
   - udev rules
   - MAC persistence
   - USB NIC firmware blobs

**Acceptance checks**:
- Moonraker patch is present in extracted rootfs
- ethernet-related files are present in extracted rootfs
- no obvious conflict with existing Wi-Fi/network files after extraction review

### Phase 5 — Implement complex features
1. Feature 2: Remote Screen
2. Feature 6: Camera FPS improvements

**Acceptance checks**:
- extracted rootfs contains:
  - fb-http service
  - nginx sidecar config
  - screen assets
  - camera init scripts
  - udev rules
  - Moonraker camera config fragments
- build succeeds with external dependencies resolved reproducibly

### Phase 6 — End-to-end validation
1. Build the final image from this repo
2. Inspect extracted rootfs before flashing
3. Flash to printer
4. Verify selected features on hardware
5. Document build and flash workflow in this repo

**Acceptance checks**:
- firmware image builds from this repo
- firmware image flashes successfully
- selected imported features behave as expected on device

---

## Risk areas
- **Architecture drift**: adding image targets to the existing `Makefile` must not break current MCU build behavior
- **Host/source mismatch**: if repo-owned host files are not copied into the image, the result will still effectively be “stock firmware plus overlays”
- **ARM64 execution requirement**: build steps that `chroot` into the ARM64 rootfs may fail on unsupported x86_64 setups
- **Remote Screen**: nginx include support and auth forwarding are easy to miswire
- **Camera**: cross-build plus external `v4l2-mpp` dependency is the most complex feature in scope
- **USB Ethernet**: network changes can reduce device recoverability if done incorrectly
- **Official firmware drift**: future Snapmaker firmware versions may require patch refreshes

---

## Rollback strategy
- keep each imported feature isolated in its own repo-owned overlay where practical
- support overlays remain separable from feature overlays
- if a feature proves unstable, remove that overlay from the build profile and rebuild
- validate changes with `make extract` before flashing
- do not flash an image that has not first been inspected after repack

---

## Exact build workflow

This is the current verified build path for the custom Snapmaker U1 firmware image in this repository.

### 1. Start the supported build environment

- Make sure the Docker daemon is running
- Use the Dockerized builder via `./dev.sh`
- Do not rely on plain host-native `make build` on macOS; the image builder needs the Linux/ARM64 chroot-capable environment from `.github/dev/Dockerfile`

### 2. Know which inputs are used

- Official base firmware comes from `vars.mk`
- The build starts from Snapmaker's stock `U1_1.2.0.106` firmware and repacks it
- Repo-owned config files under `lava/` are copied into the image by `overlays/firmware-extended/01-repo-host-sync/`
- Feature and support changes are applied through `overlays/`
- External dependencies are fetched on demand and cached under `tmp/cache/`
  - `v4l2-mpp`
  - `live555`
  - static `curl`
  - Realtek USB NIC firmware files

### 3. Build commands

Run these commands from the repo root:

```bash
./dev.sh make tools
./dev.sh make firmware
./dev.sh make build PROFILE=extended
make CPP=arm-none-eabi-cpp clean
make CPP=arm-none-eabi-cpp
install -D -m 755 out/klipper.bin out_at32f403a/at32f403a.bin
```

Useful helper commands:

```bash
make profiles
make overlays PROFILE=extended
./dev.sh make extract PROFILE=extended
```

### 4. What each command does

1. `./dev.sh make tools`
   - builds `rk2918_tools`, `upfile`, and `resource_tool`
2. `./dev.sh make firmware`
   - downloads the official Snapmaker firmware from `vars.mk`
   - verifies its SHA256
   - stores it in `firmware/`
3. `./dev.sh make build PROFILE=extended`
   - unpacks the official firmware
   - extracts the root filesystem
   - applies overlays in profile order
   - rebuilds the rootfs
   - repacks `update.img`
   - repacks the final `firmware/firmware.bin`
4. `make CPP=arm-none-eabi-cpp clean && make CPP=arm-none-eabi-cpp`
   - builds the mainboard MCU firmware binary at `out/klipper.bin`
   - `CPP=arm-none-eabi-cpp` is required on macOS (system cpp fails on `.lds.S`)
5. `install -D -m 755 out/klipper.bin out_at32f403a/at32f403a.bin`
   - copies the MCU build artifact to the dedicated flash location used in this repo

### 5. Verified overlay order for `PROFILE=extended`

The current build uses this order:

1. `overlays/common/04-nginx-fluidd.d/`
2. `overlays/common/05-persist-dhcp/`
3. `overlays/common/06-persist-eth0-mac/`
4. `overlays/common/07-persist-ssh-hostkeys/`
5. `overlays/firmware-extended/01-repo-host-sync/`
6. `overlays/firmware-extended/02-firmware-config/`
7. `overlays/firmware-extended/14-patch-firmware-files/`
8. `overlays/firmware-extended/20-disable-ipv6/`
9. `overlays/firmware-extended/21-add-curl/`
10. `overlays/firmware-extended/22-patch-moonraker/`
11. `overlays/firmware-extended/23-usb-ethernet/`
12. `overlays/firmware-extended/33-feature-timelapse-stub/`
13. `overlays/firmware-extended/60-app-camera/`
14. `overlays/firmware-extended/61-app-remote-screen/`

### 6. Build outputs to inspect

- Final packed firmware: `firmware/firmware.bin`
- Repacked SoC update image: `tmp/firmware/update.img`
- Rebuilt rootfs for inspection: `tmp/firmware/rootfs/`
- Cached downloads and source trees: `tmp/cache/`

### 7. What to inspect after changes

- Confirm final build still succeeds with:
  - `./dev.sh make build PROFILE=extended`
- Inspect rebuilt rootfs for the changed feature under:
  - `tmp/firmware/rootfs/`
- Re-check overlay order with:
  - `make overlays PROFILE=extended`
- If the change touches base config hooks, verify:
  - `tmp/firmware/rootfs/home/lava/origin_printer_data/config/printer.cfg`
  - `tmp/firmware/rootfs/home/lava/origin_printer_data/config/moonraker.conf`

### 8. Flash paths currently known

Two flash/update paths are currently documented from the official device scripts:

1. SoC-only update using the rebuilt `update.img`

```bash
scp tmp/firmware/update.img root@<u1-ip>:/tmp/
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
```

2. Full UPFILE-style update using `firmware.bin`

```bash
scp firmware/firmware.bin root@<u1-ip>:/tmp/upgrade.bin
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade all /tmp/upgrade.bin
```

Hardware flashing is still pending validation on a real device. Until that is completed, the build path above is verified, but on-device behavior must still be treated as the final acceptance gate.

3. Mainboard-MCU-only update using the separately built `at32f403a.bin`

```bash
scp out_at32f403a/at32f403a.bin root@<u1-ip>:/tmp/
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade mcu0 /tmp/at32f403a.bin
```

Important:
- `./dev.sh make build PROFILE=extended` does not rebuild `src/` MCU changes
- changes under `src/` require the separate root MCU build shown above
- host/SoC and mainboard MCU are therefore two separate build/flash paths

### 8a. Recommended first hardware flash procedure

For the first real device test, prefer the SoC-only path with `update.img`.

Reason:

- the imported changes in this repository are host/rootfs-side
- the stock MCU binaries from the official firmware are still bundled, but there is no need to reflash the MCUs for the first validation pass
- `upgrade soc` limits the blast radius compared to `upgrade all`

Recommended checklist:

1. Make sure the printer is idle
   - no active print
   - stable power
   - network access to the device is working
2. Keep both artifacts available locally
   - custom image: `tmp/firmware/update.img`
   - stock rollback source: re-extract the official image with `./dev.sh make extract PROFILE=extended` and keep the stock `update.img` from the extracted firmware if a rollback is needed
3. Verify the custom build finished successfully
   - `./dev.sh make build PROFILE=extended`
4. Copy the custom SoC image to the printer

```bash
scp tmp/firmware/update.img root@<u1-ip>:/tmp/
```

5. Run the SoC-only upgrade

```bash
ssh root@<u1-ip> /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
```

6. Wait for the device to reboot completely
7. Validate basic recovery first
   - device boots
   - web UI responds
   - Klipper and Moonraker come up
   - no immediate boot loop
8. Then validate imported features one by one
   - OEM disk usage
   - extended include hooks
   - curl
   - IPv6 disable
   - USB Ethernet
   - Remote Screen
   - Camera

Only after the SoC-only path is confirmed should `firmware.bin` / `upgrade all` be considered.

### 9. Workflow for future feature changes

1. Change repo-owned files in `overlays/`, `lava/`, `deps/`, or build scripts as needed
2. Treat this repository as the canonical U1 integration branch for future work.
   - New U1-specific behavior should land here first.
   - If porting from upstream or mainline Klipper, import only the minimum required deltas and keep them isolated in repo-owned overlays or clearly scoped source changes.
   - Re-check interactions with the Snapmaker base image and existing imported features before flashing.
   - When a host-side Klipper source file is changed, make sure the image build copies that file into `/home/lava/klipper` and record the upstream commit reference when one exists.
3. Re-run:
   - `./dev.sh make build PROFILE=extended`
4. Inspect:
   - `tmp/firmware/rootfs/`
5. If the feature affects runtime config, confirm the copied or patched files in the rebuilt rootfs
6. Only after software inspection, flash and test on hardware

---

## Implementation status
- [x] Phase 0: plan validation and correction
- [x] Phase 1: image-builder foundation
  - [x] Validate source files and required imports from `SnapmakerU1-Extended-Firmware`
  - [x] Import builder tools and helper scripts
  - [x] Integrate image targets into root `Makefile`
  - [x] Add baseline repo structure for `tools/`, `overlays/`, and image builds
  - [x] Verify imported builder commands
    - Verification result:
      - `make profiles` succeeds and lists `extended` / `extended-devel`
      - `make -n tools` dispatches into the imported tool directories correctly
      - `./dev.sh make tools` succeeds in the Docker builder
      - `./dev.sh make firmware` downloads and verifies the official base image
      - `./dev.sh make extract PROFILE=extended` unpacks the firmware and rootfs successfully
- [x] Phase 2: support layers
  - [x] Import minimal `02-firmware-config` subset
  - [x] Import `common/04-nginx-fluidd.d`
  - [x] Import `common/05-persist-dhcp`
  - [x] Import `common/06-persist-eth0-mac`
  - [x] Import `common/07-persist-ssh-hostkeys`
  - [x] Import USB NIC firmware blob support from `ccde0b8`
  - [x] Add a repo-owned host-sync overlay
  - [x] Import `timelapse` compatibility stub to avoid Moonraker/Fluidd plugin warnings with the stock `[timelapse]` printer config
- [x] Phase 3: low-risk features
  - [x] Feature 3: extended include support via `02-firmware-config`
  - [x] Feature 5: disable IPv6
  - [x] Feature 4: curl
  - [x] Full build/repack verification complete with `./dev.sh make build PROFILE=extended`
- [x] Phase 4: medium-risk features
  - [x] Feature 1: OEM Disk Usage overlay imported
  - [x] Feature 7: USB Ethernet overlays imported
  - [x] Full build/repack verification complete with `./dev.sh make build PROFILE=extended`
  - [x] Removed the stale `dhcpcd.conf` patch because stock firmware `1.2.0.106` already includes `allowinterfaces wlan0 eth0`
- [x] Phase 5: complex features
  - [x] Import Feature 2: Remote Screen overlay, assets, and screen-app dependency integration
  - [x] Import Feature 6: Camera overlay, pinned `v4l2-mpp` build, and helper apps
  - [x] Fix the imported Remote Screen HTML path inconsistency to match the installed `screen-apps` layout
  - [x] Set Remote Screen defaults to enabled in the repo-owned `extended.cfg` and default Moonraker fragment for future fresh configs
  - [x] Rename the runtime config from `extended2.cfg` to `extended.cfg` and add one-time migration logic for existing printers
  - [x] Rebuild `firmware/firmware.bin` after enabling Remote Screen defaults and fixing the `timelapse` stub
  - [x] Patch `klippy/toolhead.py` to use `LOOKAHEAD_FLUSH_TIME = 0.150` and extend host-sync so the modified host file is copied into the firmware image
  - [x] Persist Dropbear host keys in `/oem/dropbear` so the official SSH feature keeps a stable host key across reboots
  - [x] Full build/repack verification complete with `./dev.sh make build PROFILE=extended`
- [x] Phase 6: end-to-end validation
  - [x] Final firmware image builds from this repo: `firmware/firmware.bin`
  - [x] Rebuilt rootfs inspected in `tmp/firmware/rootfs`
  - [x] Flash to printer
  - [x] Verify selected features on hardware
  - [x] Document final build/flash workflow
- [x] Post-import repo planning
  - [x] Validate `motor-phase-tuning.md` against this repo and the local Prusa reference
  - [x] Recast `motor-phase-tuning.md` as a feasibility-first plan instead of an implementation-ready task list
- [ ] Motor phase tuning Phase 0
  - [x] Add an initial host-side `motor_phase_calibration` Klipper extra
  - [x] Seed repo config with `[force_move]` and `[motor_phase_calibration]`
  - [x] Extend `tmc2240.py` with `DIRECT_MODE` `coil_a` / `coil_b` fields for debug experiments
  - [x] Validate `MOTOR_PHASE_MEASURE` on hardware
  - [x] Validate `DUMP_TMC` / `SET_TMC_FIELD` direct-mode experiments on hardware
  - [x] Rebuild after the host-file permission fix and the updated measurement return-path logic
  - [x] Reflash and validate the updated measurement return-path logic on hardware
- [ ] Motor phase tuning Phase 1
  - [x] Add a first host-side CSV analyzer script for constant-speed captures
  - [x] Run the analyzer on real U1 CSV files copied off the printer
  - [x] Use the measured sample-rate envelope to define the first bounded sweep range
  - [x] Add automatic safe staging and axis-limit-aware direction handling
  - [x] Add an in-printer bounded sweep command that can sequence multiple measurements
  - [x] Add host-side forward/backward aggregation across repeated runs at the same speed
  - [x] Add host-side basis export for a first small harmonic set from aggregated runs
  - [x] Add host-side normalized fit/LUT export from the aggregated basis
  - [x] Add host-side runtime-payload export for a future U1 integration target
  - [x] Add a first printer-side direct-mode consumer for the runtime-payload prototype
  - [x] Add an in-memory profile layer for loaded runtime payloads
  - [x] Add a step-generation-adjacent diagnostic trace command that exports real `stepcompress` history for a normal move
  - [x] Add a step-execution-near diagnostic trace path with MCU-side sampled execution clocks and step numbers
  - [x] Rebuild fresh host and mainboard-MCU artifacts for the execution-trace slice
  - [x] Add an execution-near correction-plan projection path plus offline analyzer support for `...-plan.csv`
- [ ] Motor phase tuning Phase 2
  - [x] Reject the host-`dwell()` direct-mode path as too coarse for benefit testing
  - [x] Reject the scheduled-SPI direct-mode path as still audibly stuttering
  - [x] Prove that host flush and queue/load seams are too coarse, while the execution-near seam is diagnostically useful
  - [x] Stabilize the printer-side `H2/H4` execution-near gate as the current acceptance baseline
  - [ ] Define the smallest dedicated phase-stepping executor for one motor on the mainboard MCU
  - [ ] Keep the first executor limited to `stepper_y @ 30 mm/s` with the frozen `H2/H4` working set
  - [ ] Add an explicit rollback/escape path from direct mode back to normal step/dir
- [ ] Motor phase tuning Phase 3
  - [x] Build a baseline-only dedicated executor (src/motor_phase_exec.c)
    - [x] **Jitter-Proof Ring Buffer**: MCU task now drains a buffer instead of a single flag.
    - [x] **Zero-Drift Flash LUT**: 1024-point Sine table for precise baseline currents.
    - [x] **SPI Optimization**: Default frequency increased to 4MHz.
    - [x] **Sync Hardening**: Minimized host-to-MCU phase-entry latency.
    - [x] **Safety Logic**: Added GSTAT check to prevent start on undervoltage.
    - [x] timer ISR pushes to ring buffer, wakes task
    - [x] DECL_TASK writes 5-byte DIRECT_MODE SPI frame per buffer entry
    - [x] config/start/stop MCU commands
    - [x] DECL_SHUTDOWN marks executors idle
    - [x] Added to src/Makefile under CONFIG_HAVE_GPIO_SPI
- [x] Motor phase tuning Phase 4
  - [x] **Hardware Correction LUT**: Added RAM-based correction tables (corr_a, corr_b) to MCU.
  - [x] **Dynamic Table Upload**: New MCU command to stream correction data from host.
  - [x] **Real-time Summation**: MCU task now adds correction LUT to baseline sine in the SPI loop.
  - [x] **Stable Mess-Path**: Integrated accelerometer measurement into the stable EXEC_RUN path.
  - [x] Host-side MOTOR_PHASE_EXEC_RUN command in motor_phase_calibration.py
    - [x] MotorPhaseExec Python class sets up MCU SPI + executor OID at config time
    - [x] exec_stepper / exec_spi_bus / exec_cs_pin config params in printer.cfg
    - [x] reuses _with_direct_mode + _stage_xy_position for safe entry/exit
    - [x] re-homes XY automatically after run (position unknown after direct mode)
  - [x] Refactor executor from one global MCU instance to per-OID channels
  - [x] Add dual-channel CoreXY host orchestration with partner SPI config
  - [x] Add `CARRIAGE_AXIS` / `DIRECTION` handling so baseline runs can drive coherent CoreXY carriage motion instead of a single motor
  - [x] Add grouped dual-motor priming and an MCU-side startup ramp
  - [x] Fix signed `phase_advance` handling in the MCU executor
  - [x] Add a shared MCU `start_clock` and explicit `armed -> ramp -> cruise`
    executor states so both CoreXY motors begin on the same timebase
  - [x] Build MCU binary (out_at32f403a/at32f403a.bin, 48 KB)
  - [x] Stage the custom main-MCU bundle into the SoC image so `check-restore`
    no longer reverts the flashed executor MCU back to stock on reboot
  - [x] Split startup MCU version checks so `mcu0` and `head0..head3` can use
    different expected versions without disabling `check-restore`
  - [ ] Validate clean direct-mode entry/exit and no desync after the dual-motor baseline run
  - [ ] Assess whether baseline CoreXY carriage motion is audibly smooth (no stutter)
  - [ ] Only after baseline smoothness is proven, add correction-capable mode

---

## Handoff
- Agent: Claude Code
- Date: 2026-04-08
- Completed this session:
  - diagnosed root cause of "no motion" regression: previous auto-calibration
    ran at COIL_SCALE=64/EXEC_IRUN_PCT=40, which is below the accelerometer
    detection threshold — the reported offset=704 was noise, not real motion
  - implemented full architecture redesign of the calibration system:
    - new `MOTOR_PHASE_DIRECTION_PROBE` command: single-motor test with partner
      at zero current (no holding torque), total-magnitude accelerometer scoring
      so diagonal CoreXY motion is detected; verifies each motor moves before
      any partner scan; helps catch dir_pin / map_flags mismatches
    - new `_enter_xdirect_hold_zero` helper: enters xDirect at coil_a=0/coil_b=0
      (passive partner hold, no executor started)
    - new `_run_single_motor_burst` helper: runs primary executor only with
      partner in zero-current xDirect
    - new `_score_accel_magnitude` helper: total DC magnitude (sqrt(x²+y²))
      for direction-probe scoring
    - redesigned `MOTOR_PHASE_AUTO_CALIBRATE`: 3-phase adaptive process:
      1. direction probe (optional, default enabled)
      2. coarse scan: COIL_SCALE=100, SPEED=2.0, DURATION=0.20s, PHASE_STEP=32
         + SETTLE_TIME=0.5s between tests + re-home every 8 tests
      3. fine scan: PHASE_STEP//4 around best coarse result (optional, default on)
  - all changes are Python-only (no MCU rebuild required)
  - verified Python syntax
- Stopped at:
  - MCU binary (c1ca7811-dirty) built last session, still not flashed
  - SoC image not rebuilt since architecture redesign (Python changes are host-only,
    but require SoC rebuild to persist across reboots via overlay system)
- Next step:
  1. Build SoC image to bundle the new Python calibration code:
     ```bash
     cd /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper
     ./dev.sh make build PROFILE=extended
     ```
  2. Flash SoC image + mainboard MCU:
     ```bash
     scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/tmp/firmware/update.img root@192.168.178.95:/tmp/
     ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade soc /tmp/update.img
     scp /Users/ArgoMac/GitHub-Development/Snapmaker-U1-Klipper/out_at32f403a/at32f403a.bin root@192.168.178.95:/tmp/
     ssh root@192.168.178.95 /home/lava/bin/systemUpgrade.sh upgrade mcu0 /tmp/at32f403a.bin
     ssh root@192.168.178.95 reboot
     ```
  3. Verify no boot errors in Klipper log, then run direction probe first:
     ```gcode
     G28
     MOTOR_PHASE_DIRECTION_PROBE STEPPER=stepper_y CARRIAGE_AXIS=y COIL_SCALE=120 EXEC_IRUN_PCT=40
     ```
     Expected output: two scores (sign=+1, sign=-1); one should be clearly
     higher. If both are near zero, raise COIL_SCALE. If `consistent=NO`,
     investigate dir_pin or map_flags.
  4. If direction probe shows motion, run full auto-calibration:
     ```gcode
     MOTOR_PHASE_AUTO_CALIBRATE STEPPER=stepper_y CARRIAGE_AXIS=y COIL_SCALE=100 EXEC_IRUN_PCT=40
     ```
     This runs ~68 short tests (2 direction probe + 64 coarse + fine scan).
     ~25-35 minutes total including homing cadence.
  5. Use the reported parameters for a full executor run:
     ```gcode
     MOTOR_PHASE_EXEC_RUN STEPPER=stepper_y CARRIAGE_AXIS=y COIL_SCALE=100 PARTNER_PHASE_OFFSET=<result> PARTNER_INVERT_B=<result> EXEC_IRUN_PCT=40 DISTANCE=10 SPEED=2.0
     ```
- Open blockers:
  - MCU firmware on printer is stale — `out_at32f403a/at32f403a.bin` must be
    flashed before testing (contains DECL_COMMAND fix and all mpe_* commands)
  - SoC image must be rebuilt to bundle new Python calibration code
- Decisions made this session:
  - `DECL_COMMAND` macros must live in `src/motor_phase_exec.c`; proxy
    declarations in `src/stepper.c` (added by Codex) cause duplicate-command
    build errors and were removed
  - auto-calibration false positive root cause: COIL_SCALE=64/EXEC_IRUN_PCT=40
    is below the accelerometer detection threshold for the U1; minimum reliable
    detection current is approximately what COIL_SCALE=100/EXEC_IRUN_PCT=40
    provides — this is not a uv_cp risk if BREAKAWAY_MS=0
  - single-motor direction probe uses total-magnitude scoring (not axis-specific)
    because with partner at zero current, carriage moves diagonally
  - settle time between calibration bursts (default 0.5s) is required to let
    the TMC charge pump recover between tests at elevated current
  - re-home every 8 tests prevents carriage drift accumulation during long
    calibration sweeps

## Notes
- Remote Screen in the current Extended Firmware is include-driven and nginx-sidecar-based; it is not just a direct `printer.cfg` modification anymore
- Existing printers keep their current `extended/` files on upgrade; the repo now migrates `extended2.cfg` to `extended.cfg` on first boot if needed
- Camera support currently depends on an external pinned `v4l2-mpp` build
- The imported plan should preserve minimality: only the selected 7 features plus their required support layers belong in scope
- This repo is now the working base for future U1 development, including selective ports from mainline Klipper, as long as U1-specific integration remains reviewable and isolated
- New feature tracks should distinguish verified repo facts from proposed design. `motor-phase-tuning.md` is currently a feasibility plan, not an implementation-ready task list.
- Mainline Klipper reference for the lookahead flush change: `16fc46fe5ff0dbbc5188ee6a7829eee5976c1eb9` (`toolhead: Reduce LOOKAHEAD_FLUSH_TIME to 0.150 seconds`, 2025-09-30)
- Official SSH remains enabled through the stock Snapmaker UI path; this repo only persists Dropbear host keys into `/oem/dropbear` so the client host key stays stable across reboots
  - Implementation detail: boot now rewires `/etc/dropbear` to `/oem/dropbear` before any SSH startup path runs, instead of patching Snapmaker's SSH activation flow
  - The boot hook must be shipped as `S49persist-dropbear-hostkeys.sh` so BusyBox `rcS` sources it even if overlay file modes are not executable
